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
from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_DRIFT,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_SECURITY,
)
from llm.refresh_fabric import publish_context_refresh_event

logger = logging.getLogger("db.manager")

_HERE = Path(__file__).resolve().parent
_SCHEMA_PATH = _HERE / "schema.sql"
_DEFAULT_DB_PATH = _HERE / "netauto.db"

TZ_UTC8 = timezone(timedelta(hours=8))


def _node_oper_state(node_data: Dict[str, Any]) -> str:
    if not isinstance(node_data, dict):
        return "missing"
    return "down" if node_data.get("down") else "up"


def _summarize_topology_changes(previous: Optional[Dict[str, Any]], current: Dict[str, Any]) -> Dict[str, Any]:
    previous_nodes = previous.get("nodes", {}) if isinstance(previous, dict) else {}
    current_nodes = current.get("nodes", {}) if isinstance(current, dict) else {}

    added_devices: List[str] = []
    removed_devices: List[str] = []
    device_state_changes: List[Dict[str, Any]] = []

    for hostname in sorted(set(previous_nodes) | set(current_nodes)):
        previous_node = previous_nodes.get(hostname)
        current_node = current_nodes.get(hostname)

        if previous_node is None and current_node is not None:
            added_devices.append(hostname)
            device_state_changes.append(
                {
                    "hostname": hostname,
                    "change": "added",
                    "previous_state": "missing",
                    "current_state": _node_oper_state(current_node),
                    "management_ip": current_node.get("mgmt_ip"),
                }
            )
            continue

        if current_node is None and previous_node is not None:
            removed_devices.append(hostname)
            device_state_changes.append(
                {
                    "hostname": hostname,
                    "change": "removed",
                    "previous_state": _node_oper_state(previous_node),
                    "current_state": "missing",
                    "management_ip": previous_node.get("mgmt_ip"),
                }
            )
            continue

        previous_state = _node_oper_state(previous_node)
        current_state = _node_oper_state(current_node)
        if previous_state != current_state:
            device_state_changes.append(
                {
                    "hostname": hostname,
                    "change": "state_changed",
                    "previous_state": previous_state,
                    "current_state": current_state,
                    "management_ip": current_node.get("mgmt_ip") or previous_node.get("mgmt_ip"),
                }
            )

    return {
        "added_devices": added_devices,
        "removed_devices": removed_devices,
        "state_changed_devices": [
            change["hostname"]
            for change in device_state_changes
            if change.get("change") == "state_changed"
        ],
        "device_state_changes": device_state_changes[:50],
        "device_change_count": len(device_state_changes),
    }


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

        # ---- Safe migrations: add columns if missing ----
        _new_columns = [
            ("devices", "source", "TEXT DEFAULT 'manual'"),
            ("devices", "role", "TEXT DEFAULT 'network-device'"),
            ("devices", "management_subnet_mask", "TEXT"),
            ("devices", "netbox_id", "INTEGER"),
        ]
        with self._connect() as conn:
            for table, col, col_def in _new_columns:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                    logger.info(f"Database migration: added {table}.{col}")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" in str(e).lower():
                        pass
                    else:
                        logger.warning(f"Migration error for {table}.{col}: {e}")
                except Exception as e:
                    logger.warning(f"Unexpected migration error for {table}.{col}: {e}")
            
            # Add Unique Index for netbox_id
            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_netbox_id ON devices(netbox_id) WHERE netbox_id IS NOT NULL")
            except Exception as e:
                logger.warning(f"Migration error creating index: {e}")

            # M26: Compliance/baseline metadata columns on config_snapshots (safe for existing DBs)
            _m26_snapshot_cols = [
                ("config_snapshots", "snapshot_type", "TEXT DEFAULT 'snapshot'"),
                ("config_snapshots", "source_type", "TEXT"),
                ("config_snapshots", "normalized_config", "TEXT"),
                ("config_snapshots", "checksum", "TEXT"),
                ("config_snapshots", "policy_version", "INTEGER"),
                ("config_snapshots", "intent_id", "TEXT"),
                ("config_snapshots", "execution_id", "INTEGER"),
                ("config_snapshots", "approved_by", "TEXT"),
                ("config_snapshots", "verified_by", "TEXT"),
                ("config_snapshots", "verified_at", "TEXT"),
            ]
            for table, col, col_def in _m26_snapshot_cols:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                    logger.info("M26 migration: added %s.%s", table, col)
                except Exception:
                    pass

            # M26: Compliance tables. (For older DBs created before schema.sql included them.)
            # We create them here as well to keep initialization tolerant.
            try:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS policies (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        policy_id       TEXT    NOT NULL UNIQUE,
                        name            TEXT    NOT NULL,
                        description     TEXT,
                        category        TEXT    NOT NULL,
                        scope_json      TEXT    NOT NULL,
                        rules_json      TEXT    NOT NULL,
                        normalization_json TEXT,
                        thresholds_json TEXT,
                        enabled         BOOLEAN DEFAULT 1,
                        version         INTEGER DEFAULT 1,
                        created_by      TEXT,
                        updated_by      TEXT,
                        created_at      TEXT    DEFAULT (datetime('now')),
                        updated_at      TEXT    DEFAULT (datetime('now'))
                    )"""
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_policies_enabled ON policies(enabled)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_policies_category_enabled ON policies(category, enabled)")
            except Exception as e:
                logger.warning("M26 migration: create policies failed: %s", e)

            try:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS policy_audit_log (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        policy_id       INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
                        action          TEXT    NOT NULL,
                        old_value_json  TEXT,
                        new_value_json  TEXT,
                        changed_by      TEXT,
                        timestamp       TEXT    DEFAULT (datetime('now'))
                    )"""
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_policy_audit_policy_ts ON policy_audit_log(policy_id, timestamp)")
            except Exception as e:
                logger.warning("M26 migration: create policy_audit_log failed: %s", e)

            try:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS compliance_jobs (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_mode        TEXT    NOT NULL,
                        status          TEXT    DEFAULT 'running',
                        started_at      TEXT    DEFAULT (datetime('now')),
                        finished_at     TEXT,
                        summary_json    TEXT,
                        error_text      TEXT
                    )"""
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_compliance_jobs_started ON compliance_jobs(started_at)")
            except Exception as e:
                logger.warning("M26 migration: create compliance_jobs failed: %s", e)

            try:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS compliance_results (
                        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_id           INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                        baseline_snapshot_id INTEGER REFERENCES config_snapshots(id),
                        actual_snapshot_id  INTEGER REFERENCES config_snapshots(id),
                        status              TEXT    NOT NULL,
                        reason              TEXT,
                        compliance_score    INTEGER,
                        severity            TEXT,
                        drift_pct           REAL,
                        missing_lines_json  TEXT,
                        extra_lines_json    TEXT,
                        changed_lines_json  TEXT,
                        violated_policies_json TEXT,
                        checked_at          TEXT    DEFAULT (datetime('now'))
                    )"""
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_compliance_results_device_checked ON compliance_results(device_id, checked_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_compliance_results_status_checked ON compliance_results(status, checked_at DESC)")
            except Exception as e:
                logger.warning("M26 migration: create compliance_results failed: %s", e)

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
            ("jobs", "ansible_artifact_manifest", "TEXT"),
            ("jobs", "ansible_entrypoint_path", "TEXT"),
            ("config_proposals", "action_artifact_json", "TEXT"),
        ]
        with self._connect() as conn:
            for table, col, col_def in _m22_columns:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                    logger.info("M22 migration: added %s.%s", table, col)
                except Exception:
                    pass  # column already exists
            try:
                conn.execute(
                    "UPDATE users SET role = 'engineer' WHERE lower(role) = 'operator'"
                )
            except Exception:
                pass
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
    def _normalize_topology_nodes_by_ip(self, topo_dict: Dict[str, Any]) -> None:
        """
        In-place: one node per management IP; prefer hostname from device DB
        (e.g. NetBox Acc_Huawei_3 over crawler-discovered Acc_Huawei_1).
        Renames or drops duplicate nodes and updates references in connectedNode/edgeLink.
        """
        nodes = topo_dict.get("nodes") or {}
        if not isinstance(nodes, dict):
            return
        by_ip: Dict[str, List[str]] = {}
        for hostname, data in nodes.items():
            if not isinstance(data, dict):
                continue
            ip = str(data.get("mgmt_ip") or data.get("ip") or hostname).strip()
            if not ip:
                continue
            by_ip.setdefault(ip, []).append(hostname)
        # For each IP, choose canonical hostname (DB preferred)
        canonical_by_ip: Dict[str, str] = {}
        for ip, hostnames in by_ip.items():
            dev = self.get_device_by_management_ip(ip)
            if dev and dev.hostname:
                canonical_by_ip[ip] = dev.hostname.strip()
            else:
                canonical_by_ip[ip] = hostnames[0]
        # Build rename map: old_key -> canonical (when old_key != canonical)
        rename: Dict[str, str] = {}
        to_drop: List[str] = []
        for ip, hostnames in by_ip.items():
            canonical = canonical_by_ip.get(ip) or hostnames[0]
            for h in hostnames:
                if h == canonical:
                    continue
                if canonical in nodes:
                    to_drop.append(h)
                else:
                    rename[h] = canonical
        # Apply renames: node under new key, then delete old key
        for old_key, new_key in rename.items():
            if old_key not in nodes or new_key in nodes:
                continue
            nodes[new_key] = dict(nodes[old_key])
            nodes[new_key]["id"] = new_key
            nodes[new_key]["hostname"] = new_key
            nodes[new_key]["name"] = new_key
            del nodes[old_key]
            to_drop.append(old_key)
        # Map dropped hostnames to canonical (for reference updates)
        refs_replace: Dict[str, str] = dict(rename)
        for h in to_drop:
            ip = next((ip for ip, hostnames in by_ip.items() if h in hostnames), None)
            if ip:
                refs_replace[h] = canonical_by_ip.get(ip) or h
        # Drop duplicates (same IP, canonical already present)
        for h in to_drop:
            nodes.pop(h, None)
        # Update references in all nodes: connectedNode, edgeLink
        for node_data in nodes.values():
            if not isinstance(node_data, dict):
                continue
            for old_key, new_key in refs_replace.items():
                if old_key == new_key:
                    continue
                conn = node_data.get("connectedNode") or []
                if isinstance(conn, list) and old_key in conn:
                    node_data["connectedNode"] = [new_key if x == old_key else x for x in conn]
                edge = node_data.get("edgeLink") or {}
                if isinstance(edge, dict):
                    for iface, link in list(edge.items()):
                        if isinstance(link, dict) and link.get("remote_node") == old_key:
                            link["remote_node"] = new_key

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
        self._normalize_topology_nodes_by_ip(topo_dict)
        with self._topo_lock:
            previous_topology = json.loads(json.dumps(self._topo_cache))
            self._topo_cache = topo_dict
        topology_change = _summarize_topology_changes(previous_topology, topo_dict)
        publish_context_refresh_event(
            source="crawler_cache",
            action="topology_cache_replaced",
            summary=(
                f"topology cache replaced with {len(topo_dict.get('nodes', {}))} nodes; "
                f"device_changes={topology_change['device_change_count']}"
            ),
            domains=(CONTEXT_DOMAIN_LIVE_TOPOLOGY,),
            event_type="refresh",
            refresh_mode="none",
            metadata={
                "timestamp": topo_dict.get("timestamp"),
                "node_count": len(topo_dict.get("nodes", {})),
                "topology_change": {
                    "added_devices": topology_change["added_devices"],
                    "removed_devices": topology_change["removed_devices"],
                    "state_changed_devices": topology_change["state_changed_devices"],
                    "device_change_count": topology_change["device_change_count"],
                },
                "device_state_changes": topology_change["device_state_changes"],
            },
        )
        logger.debug("Topology cache updated (%d nodes)",
                      len(topo_dict.get("nodes", {})))

    def get_topology(self) -> Dict[str, Any]:
        """Return the current topology snapshot (thread-safe copy). Nodes whose
        hostname is not in the device DB are excluded so only registered devices appear."""
        with self._topo_lock:
            out = json.loads(json.dumps(self._topo_cache))
        nodes = out.get("nodes") or {}
        if not isinstance(nodes, dict):
            return out
        to_drop = []
        for hostname in nodes:
            try:
                if not self.get_device_by_hostname(hostname):
                    to_drop.append(hostname)
            except Exception:
                to_drop.append(hostname)
        for hostname in to_drop:
            nodes.pop(hostname, None)
        return out

    # =============================================================
    # CONTEXT EVENT LOG  (persistent)
    # =============================================================
    def append_context_event_log(self, publication: Dict[str, Any]) -> int:
        event_id = str(publication.get("event_id") or "")
        if not event_id:
            raise ValueError("context event publication must include event_id")

        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR REPLACE INTO context_event_log
                   (event_id, source, action, summary, event_type,
                    domains_json, versions_json, metadata_json, refresh_mode,
                    refresh_targets_json, refresh_jobs_json, listener_results_json,
                    downstream_targets_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    publication.get("source"),
                    publication.get("action"),
                    publication.get("summary"),
                    publication.get("event_type") or "invalidate",
                    json.dumps(publication.get("domains", []), ensure_ascii=False),
                    json.dumps(publication.get("versions", {}), ensure_ascii=False),
                    json.dumps(publication.get("metadata", {}), ensure_ascii=False),
                    publication.get("refresh_mode"),
                    json.dumps(publication.get("refresh_targets", []), ensure_ascii=False),
                    json.dumps(publication.get("refresh_jobs", []), ensure_ascii=False),
                    json.dumps(publication.get("listener_results", []), ensure_ascii=False),
                    json.dumps(publication.get("downstream_targets", []), ensure_ascii=False),
                    publication.get("timestamp"),
                ),
            )
            return cur.lastrowid

    def get_context_event_log(
        self,
        limit: int = 20,
        source: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if source:
            clauses.append("source = ?")
            params.append(source)
        if domain:
            clauses.append("domains_json LIKE ?")
            params.append(f'%"{domain}"%')

        query = "SELECT * FROM context_event_log"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        entries = []
        for row in rows:
            entries.append(
                {
                    "event_id": row["event_id"],
                    "source": row["source"],
                    "action": row["action"],
                    "summary": row["summary"],
                    "event_type": row["event_type"],
                    "domains": json.loads(row["domains_json"] or "[]"),
                    "versions": json.loads(row["versions_json"] or "{}"),
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                    "refresh_mode": row["refresh_mode"],
                    "refresh_targets": json.loads(row["refresh_targets_json"] or "[]"),
                    "refresh_jobs": json.loads(row["refresh_jobs_json"] or "[]"),
                    "listener_results": json.loads(row["listener_results_json"] or "[]"),
                    "downstream_targets": json.loads(row["downstream_targets_json"] or "[]"),
                    "created_at": row["created_at"],
                }
            )
        return entries

    # =============================================================
    # DEVICES  (persistent)
    # =============================================================
    def upsert_device(self, device: Device) -> int:
        """
        Insert a new device or update an existing one. Matches by ID if provided, 
        otherwise falls back to hostname.
        """
        now = datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds")
        with self._connect() as conn:
            # 1. Match by local ID
            row = None
            if device.id is not None:
                row = conn.execute("SELECT id FROM devices WHERE id = ?", (device.id,)).fetchone()
            
            # 2. Match by NetBox ID
            if not row and device.netbox_id is not None:
                row = conn.execute("SELECT id FROM devices WHERE netbox_id = ?", (device.netbox_id,)).fetchone()
            
            # 3. Match by hostname fallback
            if not row:
                row = conn.execute(
                    "SELECT id FROM devices WHERE hostname = ?",
                    (device.hostname,),
                ).fetchone()

            if row:
                conn.execute(
                    """UPDATE devices
                       SET hostname = ?, management_ip = ?, management_subnet_mask = ?, vendor = ?, model = ?,
                           os_version = ?, serial_number = ?, status = ?,
                           role = ?, source = ?, last_polled = ?, ssh_user = ?,
                           ssh_password = ?, ssh_port = ?, netbox_id = ?, updated_at = ?
                     WHERE id = ?""",
                    (
                        device.hostname, device.management_ip, device.management_subnet_mask, device.vendor, device.model,
                        device.os_version, device.serial_number, device.status,
                        device.role, device.source, device.last_polled or now,
                        device.ssh_user, device.ssh_password, device.ssh_port,
                        device.netbox_id, now, row["id"],
                    ),
                )
                return row["id"]
            else:
                cur2 = conn.execute(
                    """INSERT INTO devices
                       (hostname, management_ip, management_subnet_mask, vendor, model, os_version,
                        serial_number, status, role, source, last_polled,
                        ssh_user, ssh_password, ssh_port, netbox_id,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        device.hostname, device.management_ip, device.management_subnet_mask, device.vendor,
                        device.model, device.os_version, device.serial_number,
                        device.status, device.role, device.source, device.last_polled or now,
                        device.ssh_user, device.ssh_password, device.ssh_port, device.netbox_id,
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

    def get_device_by_management_ip(self, management_ip: str) -> Optional[Device]:
        """Return the device with this management IP, if any. When multiple rows
        share the same IP, returns the one with smallest id (first registered)."""
        if not management_ip or not str(management_ip).strip():
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE management_ip = ? ORDER BY id ASC LIMIT 1",
                (management_ip.strip(),),
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
            role=row["role"] if "role" in keys else "network-device",
            source=row["source"] if "source" in keys else "manual",
            last_polled=row["last_polled"],
            ssh_user=row["ssh_user"] if "ssh_user" in keys else None,
            ssh_password=row["ssh_password"] if "ssh_password" in keys else None,
            ssh_port=row["ssh_port"] if "ssh_port" in keys else 22,
            management_subnet_mask=row["management_subnet_mask"] if "management_subnet_mask" in keys else None,
            netbox_id=row["netbox_id"] if "netbox_id" in keys else None,
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
        captured_at = (
            snapshot.captured_at
            or datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds")
        )
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO config_snapshots
                   (device_id, config_text, config_hash, captured_at,
                    snapshot_type, source_type, normalized_config, checksum,
                    policy_version, intent_id, execution_id,
                    approved_by, verified_by, verified_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.device_id, snapshot.config_text,
                    snapshot.config_hash,
                    captured_at,
                    getattr(snapshot, "snapshot_type", None) or "snapshot",
                    getattr(snapshot, "source_type", None),
                    getattr(snapshot, "normalized_config", None),
                    getattr(snapshot, "checksum", None),
                    getattr(snapshot, "policy_version", None),
                    getattr(snapshot, "intent_id", None),
                    getattr(snapshot, "execution_id", None),
                    getattr(snapshot, "approved_by", None),
                    getattr(snapshot, "verified_by", None),
                    getattr(snapshot, "verified_at", None),
                ),
            )
            snapshot_id = cur.lastrowid

        publish_context_refresh_event(
            source="config_snapshot_writer",
            action="config_snapshot_saved",
            summary=f"config snapshot saved for device_id={snapshot.device_id}",
            domains=(CONTEXT_DOMAIN_CONFIG,),
            event_type="refresh",
            refresh_mode="none",
            metadata={
                "device_id": snapshot.device_id,
                "snapshot_id": snapshot_id,
                "captured_at": captured_at,
            },
        )
        return snapshot_id

    def get_config_snapshot(self, snapshot_id: int) -> Optional[ConfigSnapshot]:
        """Return a single config snapshot by id, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM config_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        if not row:
            return None
        keys = row.keys()
        return ConfigSnapshot(
            id=row["id"],
            device_id=row["device_id"],
            config_text=row["config_text"],
            config_hash=row["config_hash"],
            captured_at=row["captured_at"],
            snapshot_type=row["snapshot_type"] if "snapshot_type" in keys else "snapshot",
            source_type=row["source_type"] if "source_type" in keys else None,
            normalized_config=row["normalized_config"] if "normalized_config" in keys else None,
            checksum=row["checksum"] if "checksum" in keys else None,
            policy_version=row["policy_version"] if "policy_version" in keys else None,
            intent_id=row["intent_id"] if "intent_id" in keys else None,
            execution_id=row["execution_id"] if "execution_id" in keys else None,
            approved_by=row["approved_by"] if "approved_by" in keys else None,
            verified_by=row["verified_by"] if "verified_by" in keys else None,
            verified_at=row["verified_at"] if "verified_at" in keys else None,
        )

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
                snapshot_type=r["snapshot_type"] if "snapshot_type" in r.keys() else "snapshot",
                source_type=r["source_type"] if "source_type" in r.keys() else None,
                normalized_config=r["normalized_config"] if "normalized_config" in r.keys() else None,
                checksum=r["checksum"] if "checksum" in r.keys() else None,
                policy_version=r["policy_version"] if "policy_version" in r.keys() else None,
                intent_id=r["intent_id"] if "intent_id" in r.keys() else None,
                execution_id=r["execution_id"] if "execution_id" in r.keys() else None,
                approved_by=r["approved_by"] if "approved_by" in r.keys() else None,
                verified_by=r["verified_by"] if "verified_by" in r.keys() else None,
                verified_at=r["verified_at"] if "verified_at" in r.keys() else None,
            )
            for r in rows
        ]

    # =============================================================
    # COMPLIANCE: Policies, audit, results, jobs  (persistent)
    # =============================================================
    def _now_iso(self) -> str:
        return datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds")

    def create_policy(self, policy: Dict[str, Any], actor: str = "system") -> Dict[str, Any]:
        """
        Create a policy (version=1) and write an audit log entry.
        Expects `policy` fields: policy_id, name, description, category, scope, rules,
        normalization, thresholds, enabled.
        Returns the stored row as dict.
        """
        now = self._now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO policies
                   (policy_id, name, description, category, scope_json, rules_json,
                    normalization_json, thresholds_json, enabled, version, created_by, updated_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
                (
                    str(policy.get("policy_id") or "").strip(),
                    str(policy.get("name") or "").strip(),
                    policy.get("description"),
                    str(policy.get("category") or "").strip(),
                    json.dumps(policy.get("scope") or {}, ensure_ascii=False),
                    json.dumps(policy.get("rules") or [], ensure_ascii=False),
                    json.dumps(policy.get("normalization") or {}, ensure_ascii=False),
                    json.dumps(policy.get("thresholds") or {}, ensure_ascii=False),
                    1 if bool(policy.get("enabled", True)) else 0,
                    actor,
                    actor,
                    now,
                    now,
                ),
            )
            pid = cur.lastrowid
            conn.execute(
                """INSERT INTO policy_audit_log (policy_id, action, old_value_json, new_value_json, changed_by, timestamp)
                   VALUES (?, 'create', NULL, ?, ?, ?)""",
                (pid, json.dumps(policy, ensure_ascii=False), actor, now),
            )
            row = conn.execute("SELECT * FROM policies WHERE id = ?", (pid,)).fetchone()
        return dict(row) if row else {}

    def list_policies(self, include_disabled: bool = True) -> List[Dict[str, Any]]:
        q = "SELECT * FROM policies"
        params: List[Any] = []
        if not include_disabled:
            q += " WHERE enabled = 1"
        q += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [self._hydrate_policy_row(r) for r in rows]

    def get_policy_by_policy_id(self, policy_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM policies WHERE policy_id = ?", (policy_id,)).fetchone()
        return self._hydrate_policy_row(row) if row else None

    def update_policy(self, policy_id: str, patch: Dict[str, Any], actor: str = "system") -> Optional[Dict[str, Any]]:
        """
        Update a policy and increment version.
        Patch can include: name, description, category, scope, rules, normalization, thresholds, enabled.
        """
        now = self._now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM policies WHERE policy_id = ?", (policy_id,)).fetchone()
            if not row:
                return None
            old = self._hydrate_policy_row(row)
            new_obj = dict(old)
            # Apply patch
            for k in ("name", "description", "category", "enabled"):
                if k in patch:
                    new_obj[k] = patch[k]
            if "scope" in patch:
                new_obj["scope"] = patch.get("scope") or {}
            if "rules" in patch:
                new_obj["rules"] = patch.get("rules") or []
            if "normalization" in patch:
                new_obj["normalization"] = patch.get("normalization") or {}
            if "thresholds" in patch:
                new_obj["thresholds"] = patch.get("thresholds") or {}

            next_version = int(row["version"] or 1) + 1
            conn.execute(
                """UPDATE policies
                   SET name = ?, description = ?, category = ?, scope_json = ?, rules_json = ?,
                       normalization_json = ?, thresholds_json = ?, enabled = ?, version = ?,
                       updated_by = ?, updated_at = ?
                   WHERE policy_id = ?""",
                (
                    str(new_obj.get("name") or "").strip(),
                    new_obj.get("description"),
                    str(new_obj.get("category") or "").strip(),
                    json.dumps(new_obj.get("scope") or {}, ensure_ascii=False),
                    json.dumps(new_obj.get("rules") or [], ensure_ascii=False),
                    json.dumps(new_obj.get("normalization") or {}, ensure_ascii=False),
                    json.dumps(new_obj.get("thresholds") or {}, ensure_ascii=False),
                    1 if bool(new_obj.get("enabled", True)) else 0,
                    next_version,
                    actor,
                    now,
                    str(policy_id).strip(),
                ),
            )
            conn.execute(
                """INSERT INTO policy_audit_log (policy_id, action, old_value_json, new_value_json, changed_by, timestamp)
                   VALUES (?, 'update', ?, ?, ?, ?)""",
                (
                    row["id"],
                    json.dumps(old, ensure_ascii=False),
                    json.dumps(new_obj, ensure_ascii=False),
                    actor,
                    now,
                ),
            )
            row2 = conn.execute("SELECT * FROM policies WHERE policy_id = ?", (policy_id,)).fetchone()
        return self._hydrate_policy_row(row2) if row2 else None

    def delete_policy(self, policy_id: str, actor: str = "system") -> bool:
        now = self._now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM policies WHERE policy_id = ?", (policy_id,)).fetchone()
            if not row:
                return False
            old = self._hydrate_policy_row(row)
            conn.execute(
                """INSERT INTO policy_audit_log (policy_id, action, old_value_json, new_value_json, changed_by, timestamp)
                   VALUES (?, 'delete', ?, NULL, ?, ?)""",
                (row["id"], json.dumps(old, ensure_ascii=False), actor, now),
            )
            cur = conn.execute("DELETE FROM policies WHERE policy_id = ?", (policy_id,))
            return cur.rowcount > 0

    def list_policy_audit(self, policy_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            prow = conn.execute("SELECT id FROM policies WHERE policy_id = ?", (policy_id,)).fetchone()
            if not prow:
                return []
            rows = conn.execute(
                "SELECT * FROM policy_audit_log WHERE policy_id = ? ORDER BY timestamp DESC, id DESC LIMIT ?",
                (prow["id"], limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def create_compliance_job(self, run_mode: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO compliance_jobs (run_mode, status, started_at) VALUES (?, 'running', datetime('now'))",
                (run_mode,),
            )
            return cur.lastrowid

    def finish_compliance_job(self, job_id: int, status: str, summary: Optional[Dict[str, Any]] = None, error_text: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE compliance_jobs SET status = ?, finished_at = datetime('now'), summary_json = ?, error_text = ? WHERE id = ?",
                (status, json.dumps(summary or {}, ensure_ascii=False), error_text, job_id),
            )

    def insert_compliance_result(self, result: Dict[str, Any]) -> int:
        """
        Insert a compliance result row.
        Expected: device_id, baseline_snapshot_id, actual_snapshot_id, status, reason,
                  compliance_score, severity, drift_pct, missing_lines, extra_lines, changed_lines, violated_policies.
        """
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO compliance_results
                   (device_id, baseline_snapshot_id, actual_snapshot_id, status, reason,
                    compliance_score, severity, drift_pct,
                    missing_lines_json, extra_lines_json, changed_lines_json, violated_policies_json, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    result.get("device_id"),
                    result.get("baseline_snapshot_id"),
                    result.get("actual_snapshot_id"),
                    result.get("status"),
                    result.get("reason"),
                    result.get("compliance_score"),
                    result.get("severity"),
                    result.get("drift_pct"),
                    json.dumps(result.get("missing_lines") or [], ensure_ascii=False),
                    json.dumps(result.get("extra_lines") or [], ensure_ascii=False),
                    json.dumps(result.get("changed_lines") or [], ensure_ascii=False),
                    json.dumps(result.get("violated_policies") or [], ensure_ascii=False),
                ),
            )
            return cur.lastrowid

    def get_latest_compliance_results(self, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Returns the latest compliance result per device (deduplicated).
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT cr.*,
                       d.hostname AS hostname,
                       d.vendor   AS vendor,
                       d.role     AS role
                FROM compliance_results cr
                JOIN (
                    SELECT device_id, MAX(checked_at) AS max_checked
                    FROM compliance_results
                    GROUP BY device_id
                ) latest
                ON latest.device_id = cr.device_id AND latest.max_checked = cr.checked_at
                LEFT JOIN devices d ON d.id = cr.device_id
                ORDER BY cr.checked_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._hydrate_compliance_result_row(r) for r in rows]

    def get_device_compliance_history(self, device_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT cr.*, d.hostname AS hostname, d.vendor AS vendor, d.role AS role "
                "FROM compliance_results cr "
                "LEFT JOIN devices d ON d.id = cr.device_id "
                "WHERE cr.device_id = ? ORDER BY cr.checked_at DESC, cr.id DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
        return [self._hydrate_compliance_result_row(r) for r in rows]

    def get_latest_baseline_snapshot(self, device_id: int) -> Optional[ConfigSnapshot]:
        """
        Baseline selection rule: latest snapshot_type='baseline' first, else snapshot_type='golden'.
        Returns None if none exists.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM config_snapshots
                WHERE device_id = ? AND snapshot_type IN ('baseline', 'golden')
                ORDER BY
                  CASE snapshot_type WHEN 'baseline' THEN 0 ELSE 1 END,
                  captured_at DESC,
                  id DESC
                LIMIT 1
                """,
                (device_id,),
            ).fetchone()
        if not row:
            return None
        return self.get_config_snapshot(int(row["id"]))

    def _hydrate_policy_row(self, row: Optional[sqlite3.Row]) -> Dict[str, Any]:
        if not row:
            return {}
        out = dict(row)
        try:
            out["scope"] = json.loads(out.get("scope_json") or "{}")
        except Exception:
            out["scope"] = {}
        try:
            out["rules"] = json.loads(out.get("rules_json") or "[]")
        except Exception:
            out["rules"] = []
        try:
            out["normalization"] = json.loads(out.get("normalization_json") or "{}")
        except Exception:
            out["normalization"] = {}
        try:
            out["thresholds"] = json.loads(out.get("thresholds_json") or "{}")
        except Exception:
            out["thresholds"] = {}
        # Remove raw JSON columns to keep contract clean
        out.pop("scope_json", None)
        out.pop("rules_json", None)
        out.pop("normalization_json", None)
        out.pop("thresholds_json", None)
        return out

    def _hydrate_compliance_result_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        out = dict(row)
        for k, default in (
            ("missing_lines_json", []),
            ("extra_lines_json", []),
            ("changed_lines_json", []),
            ("violated_policies_json", []),
        ):
            try:
                out[k.replace("_json", "")] = json.loads(out.get(k) or "[]")
            except Exception:
                out[k.replace("_json", "")] = default
            out.pop(k, None)
        return out

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
        event_timestamp = event.timestamp or datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO drift_events (device_id, has_drift, severity, diff_text, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event.device_id, event.has_drift, event.severity, event.diff_text,
                    event_timestamp,
                ),
            )
            event_id = cur.lastrowid

        publish_context_refresh_event(
            source="drift_detector",
            action="drift_event_saved",
            summary=(
                f"drift {'detected' if event.has_drift else 'checked'} for device_id={event.device_id}"
            ),
            domains=(CONTEXT_DOMAIN_DRIFT,),
            event_type="refresh",
            refresh_mode="none",
            metadata={
                "device_id": event.device_id,
                "drift_event_id": event_id,
                "hostname": event.hostname,
                "has_drift": event.has_drift,
                "severity": event.severity,
                "timestamp": event_timestamp,
            },
        )
        return event_id

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
            event_id = cur.lastrowid

        publish_context_refresh_event(
            source="security_event_store",
            action="security_event_inserted",
            summary=f"security event recorded: {event_type}",
            domains=(CONTEXT_DOMAIN_SECURITY,),
            event_type="refresh",
            refresh_mode="none",
            metadata={
                "security_event_id": event_id,
                "event_type": event_type,
                "severity": severity,
                "source_ip": source_ip,
                "dest_ip": dest_ip,
                "device_id": device_id,
            },
        )
        return event_id

    # =============================================================
    # Utility
    # =============================================================
    def close(self) -> None:
        """Placeholder for future connection pool cleanup."""
        logger.info("DatabaseManager closed")
