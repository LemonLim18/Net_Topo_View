"""
M13b: Threat Detection — SSH / Console Login Monitor
======================================================
Detects brute-force SSH login attempts and unusual privileged-mode
access by analysing the `security_events` table populated by the
syslog receiver (syslog_receiver.py).

A brute-force burst is declared when >= *threshold* failed logins
originate from the same source IP within a sliding *window_sec* window.

Usage::

    monitor = LoginMonitor(db_manager, threshold=5, window_sec=120)
    events  = monitor.scan()           # returns new SecurityEvent objects
    monitor.scan_and_persist()         # write events to DB in one call
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from db.manager import DatabaseManager
from security.models import SecurityEvent

logger = logging.getLogger("security.threat.login_monitor")

# Syslog message patterns that indicate a failed login
_FAILED_LOGIN_KEYWORDS = (
    "authentication failed",
    "login failed",
    "bad passwords",
    "invalid password",
    "login invalid",
    "ssh: failed",
    "failed login attempt",
    "access denied",
)


class LoginMonitor:
    """
    Sliding-window SSH / Console brute-force detector.

    Parameters
    ----------
    db_manager:  M14 DatabaseManager.
    threshold:   Number of failures within *window_sec* to trigger an event.
    window_sec:  Observation window in seconds.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        threshold: int = 5,
        window_sec: int = 120,
    ):
        self.db = db_manager
        self.threshold = threshold
        self.window_sec = window_sec

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> List[SecurityEvent]:
        """
        Analyse recent syslog-derived security events for login failures.

        Returns new SecurityEvent objects (NOT yet persisted).
        """
        since = datetime.now(tz=timezone.utc) - timedelta(seconds=self.window_sec)
        raw_events = self._fetch_login_failures(since.isoformat())

        # Group by source IP
        by_ip: Dict[str, List[dict]] = defaultdict(list)
        for row in raw_events:
            src = row.get("source_ip") or "unknown"
            by_ip[src].append(row)

        alerts: List[SecurityEvent] = []
        for src_ip, failures in by_ip.items():
            count = len(failures)
            if count >= self.threshold:
                device_id = failures[0].get("device_id")
                description = (
                    f"Brute-force login detected: {count} failed SSH/login attempts "
                    f"from {src_ip} within {self.window_sec}s (threshold={self.threshold})."
                )
                severity = "critical" if count >= self.threshold * 3 else "high"
                alerts.append(
                    SecurityEvent(
                        event_type="brute_force",
                        severity=severity,
                        source_ip=src_ip,
                        dest_ip="",
                        device_id=device_id,
                        description=description,
                        raw_data={"failure_count": count, "window_sec": self.window_sec},
                        status="new",
                    )
                )
                logger.warning(
                    "Login brute-force: %d failures from %s in %ds",
                    count, src_ip, self.window_sec,
                )

        return alerts

    def scan_and_persist(self) -> List[int]:
        """Run scan() and write each result to the security_events table."""
        alerts = self.scan()
        ids: List[int] = []
        for ev in alerts:
            eid = self._persist(ev)
            ids.append(eid)
        return ids

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_login_failures(self, since_iso: str) -> List[dict]:
        """
        Query the security_events table for failed-login syslog entries
        created after *since_iso*.
        """
        try:
            with self.db._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT source_ip, device_id, description, created_at
                    FROM security_events
                    WHERE event_type IN ('syslog', 'auth_failure', 'brute_force')
                      AND created_at >= ?
                    ORDER BY created_at ASC
                    """,
                    (since_iso,),
                ).fetchall()
        except Exception as exc:
            logger.error("LoginMonitor DB query failed: %s", exc)
            return []

        # Filter to actual login-failure messages
        failures = []
        for row in rows:
            desc = (row["description"] or "").lower()
            if any(kw in desc for kw in _FAILED_LOGIN_KEYWORDS):
                failures.append(dict(row))
        return failures

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
