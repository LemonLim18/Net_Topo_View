"""
M8 Drift Detection — Alert Generator.

Converts MEDIUM / HIGH / CRITICAL drift findings into SecurityEvent
records so they surface in the Security Dashboard and can trigger
incident response playbooks (M22).
"""

import logging
from datetime import datetime
from typing import List, Optional

from db.manager import DatabaseManager
from drift.models import DriftEvent
from security.models import SecurityEvent

logger = logging.getLogger("drift.alerts")


class DriftAlertGenerator:
    """
    Writes SecurityEvent rows for significant configuration drift.

    Severity mapping
    ----------------
    low      → skip (informational only)
    medium   → SecurityEvent severity="medium"
    high     → SecurityEvent severity="high"
    critical → SecurityEvent severity="critical"
    """

    _SEVERITY_MAP = {
        "medium":   "medium",
        "high":     "high",
        "critical": "critical",
    }

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def emit(self, event: DriftEvent) -> Optional[int]:
        """
        Persist a SecurityEvent for a drift finding if its severity warrants it.

        Returns the new security_event id, or None if no alert was generated.
        """
        if not event.has_drift:
            return None

        mapped_sev = self._SEVERITY_MAP.get(event.severity.lower())
        if mapped_sev is None:
            # low severity → do not create a security event
            return None

        # Build the rationale text, using AI rationale if available
        rationale = event.__dict__.get("ai_rationale", "")
        risk_areas = event.__dict__.get("risk_areas", [])

        description = (
            f"Configuration drift detected on {event.hostname}. "
            f"Severity: {event.severity.upper()}."
        )
        if rationale:
            description += f" {rationale}"
        if risk_areas:
            description += f" Risk areas: {', '.join(risk_areas)}."

        sec_event = SecurityEvent(
            event_type="config_drift",
            severity=mapped_sev,
            device_id=event.device_id,
            source_ip="",
            dest_ip="",
            description=description,
            raw_data={
                "hostname":   event.hostname,
                "timestamp":  event.timestamp,
                "diff_text":  event.diff_text[:2000],  # truncate to keep DB lean
                "risk_areas": risk_areas,
            },
            status="new",
        )

        event_id = self._save(sec_event)
        logger.info(
            "Drift alert created for %s (severity=%s) → security_event #%d",
            event.hostname, mapped_sev, event_id,
        )
        return event_id

    def emit_batch(self, events: List[DriftEvent]) -> List[int]:
        """Emit alerts for a list of drift events; returns list of created IDs."""
        ids = []
        for ev in events:
            eid = self.emit(ev)
            if eid is not None:
                ids.append(eid)
        return ids

    # ------------------------------------------------------------------
    # Persistence helper
    # ------------------------------------------------------------------

    def _save(self, event: SecurityEvent) -> int:
        """Write the SecurityEvent to the DB and return its row id."""
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
                    datetime.utcnow().isoformat(),
                ),
            )
            return cur.lastrowid
