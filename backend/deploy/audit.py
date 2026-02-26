"""
M4 Deployment Pipeline — Immutable Audit Log.

Every significant pipeline event (create, approve, reject, apply, rollback,
complete, fail) is written to the `audit_log` table.  Entries are never
updated or deleted — only appended.

The AuditLogger is called directly by pipeline.py, approval.py, and any other
actor that mutates a change request or job.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from db.manager import DatabaseManager

logger = logging.getLogger("deploy.audit")

# Ensure the table exists (idempotent DDL run at first import)
_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    action      TEXT    NOT NULL,
    actor       TEXT    NOT NULL DEFAULT 'system',
    request_id  INTEGER,
    job_id      INTEGER,
    comment     TEXT,
    metadata    TEXT            -- JSON blob for extra context
);
"""


class AuditLogger:
    """
    Append-only audit trail for all M4 pipeline actions.

    Usage
    -----
    al = AuditLogger(db_manager)
    al.log("approve", request_id=5, actor="alice", comment="Approved after CAB review")
    al.log("apply",   request_id=5, job_id=12, actor="pipeline",
           metadata={"vendor": "cisco_ios", "device": "R1"})
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self.db._connect() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log(
        self,
        action: str,
        *,
        actor: str = "system",
        request_id: Optional[int] = None,
        job_id: Optional[int] = None,
        comment: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Write one audit entry and return its row id.

        Parameters
        ----------
        action:     Short verb — 'create' | 'submit' | 'approve' | 'reject' |
                    'apply' | 'verify_pass' | 'verify_fail' | 'rollback' |
                    'complete' | 'fail'
        actor:      Username or service that performed the action.
        request_id: The change request involved (if any).
        job_id:     The specific job involved (if any).
        comment:    Free-text note.
        metadata:   Extra structured data (serialised to JSON).
        """
        meta_str = json.dumps(metadata) if metadata else None
        ts = datetime.now(tz=timezone.utc).isoformat()

        with self.db._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_log (created_at, action, actor, request_id, job_id, comment, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, action, actor, request_id, job_id, comment, meta_str),
            )
            row_id = cur.lastrowid

        logger.debug(
            "AUDIT #%d | action=%s actor=%s request=%s job=%s",
            row_id, action, actor, request_id, job_id,
        )
        return row_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_log_for_request(self, request_id: int) -> list:
        """Return all audit entries for a given change request."""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE request_id = ? ORDER BY id ASC",
                (request_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_log_for_job(self, job_id: int) -> list:
        """Return all audit entries for a specific job."""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE job_id = ? ORDER BY id ASC",
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent(self, limit: int = 100) -> list:
        """Return the *limit* most recent entries across all requests."""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
