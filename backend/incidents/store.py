"""
M25 Phase-2: Incident persistence helper.

Thin wrapper around SQLite so api.py stays clean.
All IDs are UUID strings prefixed with "INC-".
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from llm.context_contract import CONTEXT_DOMAIN_INCIDENTS
from llm.refresh_fabric import publish_context_refresh_event

logger = logging.getLogger("incidents.store")

_INC_PREFIX = "INC"


def _new_id() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m%d")
    short = str(uuid.uuid4())[:8].upper()
    return f"{_INC_PREFIX}-{now}-{short}"


def _row_to_dict(row) -> dict:
    """sqlite3.Row → plain dict."""
    return dict(row)


def _invalidate_incident_context(summary: str, metadata: dict) -> None:
    publish_context_refresh_event(
        source="incident_store",
        action="incident_state_changed",
        summary=summary,
        domains=(CONTEXT_DOMAIN_INCIDENTS,),
        event_type="refresh",
        refresh_mode="none",
        metadata=metadata,
    )


class IncidentStore:
    """
    Lightweight SQLite-backed store for IncidentReport rows.

    The store auto-runs the migration on first use so it works even if
    backend.py hasn't run the migration file yet.
    """

    def __init__(self, db_path: str = "db/netauto.db"):
        self._db_path = db_path
        self._ensure_table()

    # ── internal ────────────────────────────────────────────────────────────

    def _connect(self):
        import sqlite3
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_table(self):
        ddl = """
        CREATE TABLE IF NOT EXISTS incidents (
            id                   TEXT PRIMARY KEY,
            anomaly_type         TEXT NOT NULL,
            severity             TEXT NOT NULL DEFAULT 'medium',
            affected_devices     TEXT,
            metrics_current      REAL,
            metrics_baseline     REAL,
            metrics_threshold    REAL,
            detected_at          TEXT DEFAULT (datetime('now')),
            logs                 TEXT,
            configs              TEXT,
            telemetry_snapshot   TEXT,
            root_cause_summary   TEXT,
            root_cause_confidence REAL DEFAULT 0.0,
            possible_causes      TEXT,
            proposed_description TEXT,
            proposed_playbook    TEXT,
            estimated_impact     TEXT,
            rollback_plan        TEXT,
            status               TEXT DEFAULT 'open',
            approver             TEXT,
            resolved_at          TEXT,
            created_at           TEXT DEFAULT (datetime('now')),
            updated_at           TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_incidents_status   ON incidents(status);
        CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
        CREATE INDEX IF NOT EXISTS idx_incidents_created  ON incidents(created_at);
        """
        try:
            with self._connect() as conn:
                conn.executescript(ddl)
        except Exception as exc:
            logger.error("IncidentStore init failed: %s", exc)

    # ── public API ───────────────────────────────────────────────────────────

    def create(
        self,
        anomaly_type: str,
        severity: str,
        affected_devices: List[str],
        metrics: dict | None = None,
        detected_at: str | None = None,
    ) -> str:
        """Create a new open incident, return its id."""
        inc_id = _new_id()
        now = datetime.now(timezone.utc).isoformat()
        m = metrics or {}
        sql = """
        INSERT INTO incidents
            (id, anomaly_type, severity, affected_devices,
             metrics_current, metrics_baseline, metrics_threshold,
             detected_at, status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,  'open', ?,?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                inc_id, anomaly_type, severity,
                json.dumps(affected_devices),
                m.get("current"), m.get("baseline"), m.get("threshold"),
                detected_at or now, now, now,
            ))
        logger.info("Created incident %s (%s/%s)", inc_id, anomaly_type, severity)
        _invalidate_incident_context(
            summary=f"incident created: {inc_id}",
            metadata={
                "incident_id": inc_id,
                "anomaly_type": anomaly_type,
                "severity": severity,
                "affected_devices": affected_devices,
            },
        )
        return inc_id

    def update_analysis(
        self,
        inc_id: str,
        *,
        logs: list | None = None,
        configs: dict | None = None,
        telemetry: dict | None = None,
        root_cause_summary: str = "",
        confidence: float = 0.0,
        possible_causes: list | None = None,
        proposed_description: str = "",
        proposed_playbook: str = "",
        estimated_impact: str = "",
        rollback_plan: str = "",
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        sql = """
        UPDATE incidents SET
            logs=?, configs=?, telemetry_snapshot=?,
            root_cause_summary=?, root_cause_confidence=?, possible_causes=?,
            proposed_description=?, proposed_playbook=?,
            estimated_impact=?, rollback_plan=?,
            status='investigating', updated_at=?
        WHERE id=?
        """
        with self._connect() as conn:
            cur = conn.execute(sql, (
                json.dumps(logs or []),
                json.dumps(configs or {}),
                json.dumps(telemetry or {}),
                root_cause_summary, confidence,
                json.dumps(possible_causes or []),
                proposed_description, proposed_playbook,
                estimated_impact, rollback_plan,
                now, inc_id,
            ))
        updated = cur.rowcount > 0
        if updated:
            _invalidate_incident_context(
                summary=f"incident updated: {inc_id}",
                metadata={
                    "incident_id": inc_id,
                    "status": "investigating",
                    "has_logs": bool(logs),
                    "has_configs": bool(configs),
                    "has_telemetry": bool(telemetry),
                },
            )
        return updated

    def set_status(
        self,
        inc_id: str,
        status: str,
        approver: str | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        resolved_at = now if status in ("resolved", "dismissed") else None
        sql = (
            "UPDATE incidents SET status=?, approver=?, resolved_at=?, updated_at=? WHERE id=?"
        )
        with self._connect() as conn:
            cur = conn.execute(sql, (status, approver, resolved_at, now, inc_id))
        updated = cur.rowcount > 0
        if updated:
            _invalidate_incident_context(
                summary=f"incident status changed: {inc_id} -> {status}",
                metadata={
                    "incident_id": inc_id,
                    "status": status,
                    "approver": approver,
                    "resolved_at": resolved_at,
                },
            )
        return updated

    def get(self, inc_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM incidents WHERE id=?", (inc_id,)
            ).fetchone()
        return _row_to_dict(row) if row else None

    def list_all(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> List[dict]:
        if status:
            sql = "SELECT * FROM incidents WHERE status=? ORDER BY created_at DESC LIMIT ?"
            args = (status, limit)
        else:
            sql = "SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?"
            args = (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_row_to_dict(r) for r in rows]
