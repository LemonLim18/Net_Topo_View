"""
M13b: Threat Detection — Routing Table Monitor
================================================
Detects unexpected route additions, withdrawals, and prefix hijacking by
comparing the current routing table snapshot against a saved baseline.

How it works
------------
1. Call ``snapshot()`` to save the current routing table for a device.
2. Periodically call ``compare(device_id)`` to diff the current table
   against the last saved snapshot.
3. Any new prefixes added or legitimate prefixes missing generate a
   SecurityEvent (anomaly / route_hijack).

Routing table data comes from the M1 driver layer (``get_routing_table()``).

Usage::

    monitor = RouteMonitor(db_manager)
    monitor.snapshot(device_id=1)             # baseline
    events = monitor.compare(device_id=1)     # detect deviations
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from db.manager import DatabaseManager
from drivers.factory import DriverFactory
from security.models import SecurityEvent

logger = logging.getLogger("security.threat.route_monitor")

_DDL = """
CREATE TABLE IF NOT EXISTS route_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL,
    prefixes    TEXT    NOT NULL,   -- JSON list of prefix strings
    created_at  TEXT    NOT NULL
);
"""


class RouteMonitor:
    """
    Detects routing table anomalies: hijacking, prefix injection, route removal.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self.db._connect() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self, device_id: int) -> Optional[int]:
        """
        Fetch the current routing table from the device and save it as
        the reference baseline.

        Returns the snapshot row id, or None on failure.
        """
        device = self.db.get_device_by_id(device_id)
        if device is None:
            logger.error("Device #%d not found", device_id)
            return None

        try:
            driver = DriverFactory.get_driver(device)
            prefixes = driver.get_routing_table()   # returns List[str]
        except Exception as exc:
            logger.error("Failed to fetch routing table from %s: %s", device.hostname, exc)
            return None

        return self._save_snapshot(device_id, prefixes)

    # ------------------------------------------------------------------
    # Compare
    # ------------------------------------------------------------------

    def compare(self, device_id: int) -> List[SecurityEvent]:
        """
        Compare the current routing table against the latest snapshot.

        Returns SecurityEvent objects for any deviation detected.
        """
        device = self.db.get_device_by_id(device_id)
        if device is None:
            logger.error("Device #%d not found", device_id)
            return []

        baseline = self._get_latest_snapshot(device_id)
        if baseline is None:
            logger.info("No route baseline for device #%d — take a snapshot first", device_id)
            return []

        try:
            driver = DriverFactory.get_driver(device)
            current_prefixes: Set[str] = set(driver.get_routing_table())
        except Exception as exc:
            logger.error("Could not fetch routing table from %s: %s", device.hostname, exc)
            return []

        baseline_prefixes: Set[str] = set(baseline)
        added   = current_prefixes - baseline_prefixes
        removed = baseline_prefixes - current_prefixes

        events: List[SecurityEvent] = []

        if added:
            desc = (
                f"Unexpected route injection on {device.hostname}: "
                f"{len(added)} new prefix(es) appeared: {', '.join(sorted(added)[:10])}"
                + (" ..." if len(added) > 10 else "")
            )
            events.append(SecurityEvent(
                event_type="route_hijack",
                severity="high",
                source_ip="",
                dest_ip="",
                device_id=device_id,
                description=desc,
                raw_data={"added": sorted(added), "device": device.hostname},
                status="new",
            ))
            logger.warning("Route injection on %s: %d new prefix(es)", device.hostname, len(added))

        if removed:
            desc = (
                f"Route withdrawal on {device.hostname}: "
                f"{len(removed)} prefix(es) missing: {', '.join(sorted(removed)[:10])}"
                + (" ..." if len(removed) > 10 else "")
            )
            events.append(SecurityEvent(
                event_type="anomaly",
                severity="medium",
                source_ip="",
                dest_ip="",
                device_id=device_id,
                description=desc,
                raw_data={"removed": sorted(removed), "device": device.hostname},
                status="new",
            ))
            logger.info("Route withdrawal on %s: %d prefix(es) gone", device.hostname, len(removed))

        return events

    def compare_and_persist(self, device_id: int) -> List[int]:
        """Run compare() and write events to the security_events table."""
        events = self.compare(device_id)
        ids: List[int] = []
        for ev in events:
            eid = self._persist(ev)
            ids.append(eid)
        # Update snapshot to latest after comparison
        self.snapshot(device_id)
        return ids

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_snapshot(self, device_id: int, prefixes) -> int:
        with self.db._connect() as conn:
            cur = conn.execute(
                "INSERT INTO route_snapshots (device_id, prefixes, created_at) VALUES (?, ?, ?)",
                (device_id, json.dumps(list(prefixes)),
                 datetime.now(tz=timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def _get_latest_snapshot(self, device_id: int) -> Optional[list]:
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT prefixes FROM route_snapshots WHERE device_id = ? ORDER BY id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
        return json.loads(row["prefixes"]) if row else None

    def _persist(self, event: SecurityEvent) -> int:
        with self.db._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO security_events
                    (event_type, severity, source_ip, dest_ip, device_id,
                     description, raw_data, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_type,
                    event.severity,
                    event.source_ip,
                    event.dest_ip,
                    event.device_id,
                    event.description,
                    str(event.raw_data),
                    event.status,
                    datetime.now(tz=timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid
