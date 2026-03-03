"""
M14: Database Manager
=====================
Provides two storage tiers:

1.  **Persistent (SQLite)** — Device inventory, configuration snapshots,
    audit logs.  Data survives restarts.
2.  **In-Memory Cache** — Real-time topology state (nodes, links, interface
    status).  Rebuilt automatically by the crawler on every poll cycle.
    The frontend reads this cache for the live dashboard.

The manager is designed to be database-agnostic:
    - Dev  → sqlite:///backend/db/netauto.db
    - Prod → postgresql://user:pass@host/netauto  (swap connection string)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (
    AuditLogEntry,
    ConfigSnapshot,
    Device,
    DriftEvent,
    Interface,
    Link,
    TopologyNode,
)

logger = logging.getLogger("db.manager")

_HERE = Path(__file__).resolve().parent
_SCHEMA_PATH = _HERE / "schema.sql"
_DEFAULT_DB_PATH = _HERE / "netauto.db"

TZ_UTC8 = timezone(timedelta(hours=8))


class DatabaseManager:
    """
    Unified access point for persistent storage and the real-time
    topology cache.

    Usage::

        db = DatabaseManager()          # uses default SQLite path
        db.initialize()                 # creates tables if needed

        # --- persistent operations ---
        db.upsert_device(Device(hostname="R1", management_ip="10.0.0.1"))
        devices = db.get_all_devices()

        # --- real-time cache ---
        db.update_topology(topo_dict)   # called by the crawler
        topo = db.get_topology()        # called by the frontend API
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or str(_DEFAULT_DB_PATH)
        self._lock = threading.Lock()

        # ---- Real-time topology cache (in-memory) ----
        self._topo_cache: Dict[str, Any] = {
            "timestamp": None,
            "seeds": [],
            "nodes": {},
        }
        self._topo_lock = threading.Lock()

        # ---- Safe migration: add 'source' column if missing ----
        try:
            with self._connect() as conn:
                conn.execute("ALTER TABLE devices ADD COLUMN source TEXT DEFAULT 'manual'")
        except Exception:
            pass  # column already exists

    # =============================================================
    # Initialisation
    # =============================================================
    def initialize(self) -> None:
        """Create all tables defined in schema.sql (idempotent)."""
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(schema_sql)
        # Enable WAL mode for better concurrency under parallel reads/writes
        # (background tasks, threadpool workers, scheduler all share the file)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        # M22: add new columns to existing databases (ALTER TABLE is not
        # idempotent in SQLite, so we swallow the "duplicate column" error)
        _m22_columns = [
            ("jobs", "execution_engine", "TEXT DEFAULT 'ssh_fallback'"),
            ("jobs", "ansible_playbook", "TEXT"),
        ]
        with self._connect() as conn:
            for table, col, col_def in _m22_columns:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                    logger.info("M22 migration: added %s.%s", table, col)
                except Exception:
                    pass  # column already exists
        # M0: SSH credential columns on devices
        _m0_columns = [
            ("devices", "ssh_user",     "TEXT"),
            ("devices", "ssh_password", "TEXT"),
            ("devices", "ssh_port",     "INTEGER DEFAULT 22"),
        ]
        with self._connect() as conn:
            for table, col, col_def in _m0_columns:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                    logger.info("M0 migration: added %s.%s", table, col)
                except Exception:
                    pass  # column already exists
        logger.info("Database initialised at %s", self._db_path)

    @contextmanager
    def _connect(self):
        """Yield a sqlite3 connection with row_factory and FK support."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # =============================================================
    # REAL-TIME TOPOLOGY CACHE  (in-memory, not persisted)
    # =============================================================
    def update_topology(self, topo_dict: Dict[str, Any]) -> None:
        """
        Replace the live topology cache with a fresh crawl result.

        ``topo_dict`` should match the shape that ``mini_nce.py``
        currently produces::

            {
              "timestamp": "...",
              "seeds": [...],
              "nodes": { "<hostname>": { ... }, ... }
            }
        """
        with self._topo_lock:
            self._topo_cache = topo_dict
        logger.debug("Topology cache updated (%d nodes)",
                      len(topo_dict.get("nodes", {})))

    def get_topology(self) -> Dict[str, Any]:
        """Return the current topology snapshot (thread-safe copy)."""
        with self._topo_lock:
            return json.loads(json.dumps(self._topo_cache))

    # =============================================================
    # DEVICES  (persistent)
    # =============================================================
    def upsert_device(self, device: Device) -> int:
        """
        Insert a new device or update an existing one (matched by hostname).
        Returns the device row id.
        """
        now = datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT id, source FROM devices WHERE hostname = ?",
                (device.hostname,),
            )
            row = cur.fetchone()
            if row:
                conn.execute(
                    """UPDATE devices
                       SET management_ip = ?, vendor = ?, model = ?,
                           os_version = ?, serial_number = ?, status = ?,
                           source = ?, last_polled = ?, ssh_user = ?,
                           ssh_password = ?, ssh_port = ?, updated_at = ?
                     WHERE id = ?""",
                    (
                        device.management_ip, device.vendor, device.model,
                        device.os_version, device.serial_number, device.status,
                        device.source, device.last_polled or now,
                        device.ssh_user, device.ssh_password, device.ssh_port,
                        now, row["id"],
                    ),
                )
                return row["id"]
            else:
                cur2 = conn.execute(
                    """INSERT INTO devices
                       (hostname, management_ip, vendor, model, os_version,
                        serial_number, status, source, last_polled,
                        ssh_user, ssh_password, ssh_port,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        device.hostname, device.management_ip, device.vendor,
                        device.model, device.os_version, device.serial_number,
                        device.status, device.source, device.last_polled or now,
                        device.ssh_user, device.ssh_password, device.ssh_port,
                        now, now,
                    ),
                )
                return cur2.lastrowid

    def get_device_by_hostname(self, hostname: str) -> Optional[Device]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE hostname = ?", (hostname,)
            ).fetchone()
        return self._row_to_device(row) if row else None

    def get_device_by_id(self, device_id: int) -> Optional[Device]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
        return self._row_to_device(row) if row else None

    def get_all_devices(self) -> List[Device]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM devices ORDER BY hostname").fetchall()
        return [self._row_to_device(r) for r in rows]

    def delete_device(self, device_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
            return cur.rowcount > 0

    @staticmethod
    def _row_to_device(row: sqlite3.Row) -> Device:
        keys = row.keys()
        return Device(
            id=row["id"],
            hostname=row["hostname"],
            management_ip=row["management_ip"],
            vendor=row["vendor"],
            model=row["model"],
            os_version=row["os_version"],
            serial_number=row["serial_number"],
            status=row["status"],
            source=row["source"] if "source" in keys else "manual",
            last_polled=row["last_polled"],
            ssh_user=row["ssh_user"] if "ssh_user" in keys else None,
            ssh_password=row["ssh_password"] if "ssh_password" in keys else None,
            ssh_port=row["ssh_port"] if "ssh_port" in keys else 22,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # =============================================================
    # INTERFACES  (persistent)
    # =============================================================
    def upsert_interface(self, iface: Interface) -> int:
        now = datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT id FROM interfaces WHERE device_id = ? AND name = ?",
                (iface.device_id, iface.name),
            )
            row = cur.fetchone()
            if row:
                conn.execute(
                    """UPDATE interfaces
                       SET ip_address = ?, subnet_mask = ?, mac_address = ?,
                           speed = ?, status = ?, updated_at = ?
                     WHERE id = ?""",
                    (
                        iface.ip_address, iface.subnet_mask, iface.mac_address,
                        iface.speed, iface.status, now, row["id"],
                    ),
                )
                return row["id"]
            else:
                cur2 = conn.execute(
                    """INSERT INTO interfaces
                       (device_id, name, ip_address, subnet_mask, mac_address,
                        speed, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        iface.device_id, iface.name, iface.ip_address,
                        iface.subnet_mask, iface.mac_address, iface.speed,
                        iface.status, now, now,
                    ),
                )
                return cur2.lastrowid

    def get_interfaces_for_device(self, device_id: int) -> List[Interface]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM interfaces WHERE device_id = ? ORDER BY name",
                (device_id,),
            ).fetchall()
        return [self._row_to_interface(r) for r in rows]

    @staticmethod
    def _row_to_interface(row: sqlite3.Row) -> Interface:
        return Interface(
            id=row["id"],
            device_id=row["device_id"],
            name=row["name"],
            ip_address=row["ip_address"],
            subnet_mask=row["subnet_mask"],
            mac_address=row["mac_address"],
            speed=row["speed"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # =============================================================
    # LINKS  (persistent)
    # =============================================================
    def upsert_link(self, link: Link) -> int:
        now = datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                """SELECT id FROM links
                   WHERE src_device_id = ? AND src_interface_id IS ?
                     AND dst_device_id = ? AND dst_interface_id IS ?""",
                (link.src_device_id, link.src_interface_id,
                 link.dst_device_id, link.dst_interface_id),
            )
            row = cur.fetchone()
            if row:
                conn.execute(
                    """UPDATE links SET discovery_protocol = ?, updated_at = ?
                     WHERE id = ?""",
                    (link.discovery_protocol, now, row["id"]),
                )
                return row["id"]
            else:
                cur2 = conn.execute(
                    """INSERT INTO links
                       (src_device_id, src_interface_id, dst_device_id,
                        dst_interface_id, discovery_protocol, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        link.src_device_id, link.src_interface_id,
                        link.dst_device_id, link.dst_interface_id,
                        link.discovery_protocol, now, now,
                    ),
                )
                return cur2.lastrowid

    def get_links_for_device(self, device_id: int) -> List[Link]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM links
                   WHERE src_device_id = ? OR dst_device_id = ?""",
                (device_id, device_id),
            ).fetchall()
        return [self._row_to_link(r) for r in rows]

    def get_all_links(self) -> List[Link]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM links").fetchall()
        return [self._row_to_link(r) for r in rows]

    @staticmethod
    def _row_to_link(row: sqlite3.Row) -> Link:
        return Link(
            id=row["id"],
            src_device_id=row["src_device_id"],
            src_interface_id=row["src_interface_id"],
            dst_device_id=row["dst_device_id"],
            dst_interface_id=row["dst_interface_id"],
            discovery_protocol=row["discovery_protocol"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # =============================================================
    # CONFIG SNAPSHOTS  (persistent)
    # =============================================================
    def save_config_snapshot(self, snapshot: ConfigSnapshot) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO config_snapshots
                   (device_id, config_text, config_hash, captured_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    snapshot.device_id, snapshot.config_text,
                    snapshot.config_hash,
                    snapshot.captured_at
                    or datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds"),
                ),
            )
            return cur.lastrowid

    def get_config_history(self, device_id: int, limit: int = 10) -> List[ConfigSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM config_snapshots
                   WHERE device_id = ?
                   ORDER BY captured_at DESC LIMIT ?""",
                (device_id, limit),
            ).fetchall()
        return [
            ConfigSnapshot(
                id=r["id"],
                device_id=r["device_id"],
                config_text=r["config_text"],
                config_hash=r["config_hash"],
                captured_at=r["captured_at"],
            )
            for r in rows
        ]

    # =============================================================
    # AUDIT LOG  (persistent, append-only)
    # =============================================================
    def log_action(self, entry: AuditLogEntry) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO audit_log (user_id, action, target, details, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    entry.user_id, entry.action, entry.target, entry.details,
                    entry.created_at
                    or datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds"),
                ),
            )
            return cur.lastrowid

    def get_audit_log(self, limit: int = 50) -> List[AuditLogEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AuditLogEntry(
                id=r["id"],
                user_id=r["user_id"],
                action=r["action"],
                target=r["target"],
                details=r["details"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # =============================================================
    # DRIFT EVENTS (persistent)
    # =============================================================
    def save_drift_event(self, event: DriftEvent) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO drift_events (device_id, has_drift, severity, diff_text, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event.device_id, event.has_drift, event.severity, event.diff_text,
                    event.timestamp or datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds"),
                ),
            )
            return cur.lastrowid

    def get_drift_events(self, device_id: Optional[int] = None, limit: int = 50) -> List[DriftEvent]:
        with self._connect() as conn:
            if device_id:
                query = "SELECT * FROM drift_events WHERE device_id = ? ORDER BY timestamp DESC LIMIT ?"
                params = (device_id, limit)
            else:
                query = "SELECT * FROM drift_events ORDER BY timestamp DESC LIMIT ?"
                params = (limit,)
            
            rows = conn.execute(query, params).fetchall()
        
        events = []
        for r in rows:
            # Query hostname if needed for complete model hydration
            # For efficiency we might want a JOIN in the SQL query instead
            dev = self.get_device_by_id(r["device_id"])
            hostname = dev.hostname if dev else "unknown"
            
            events.append(DriftEvent(
                id=r["id"],
                device_id=r["device_id"],
                hostname=hostname,
                has_drift=bool(r["has_drift"]),
                severity=r["severity"],
                diff_text=r["diff_text"],
                timestamp=r["timestamp"]
            ))
        return events

    # =============================================================
    # SECURITY EVENTS (persistent)
    # =============================================================
    def insert_security_event(
        self,
        event_type: str,
        severity: str,
        description: str,
        source_ip: Optional[str] = None,
        dest_ip: Optional[str] = None,
        device_id: Optional[int] = None,
        raw_data: Optional[str] = None,
    ) -> int:
        """Insert a security event (e.g. rogue_device, brute_force)."""
        now = datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO security_events
                   (event_type, severity, source_ip, dest_ip,
                    device_id, description, raw_data, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
                (event_type, severity, source_ip, dest_ip,
                 device_id, description, raw_data, now),
            )
            return cur.lastrowid

    # =============================================================
    # Utility
    # =============================================================
    def close(self) -> None:
        """Placeholder for future connection pool cleanup."""
        logger.info("DatabaseManager closed")
