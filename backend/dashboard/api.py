"""
Dashboard summary API — single endpoint that aggregates operational KPIs
from devices, interfaces, security audits, drift events, deployments,
security events, and audit log.

Designed to feed the NMC-style landing page in one round-trip.
"""

import logging
from typing import Optional

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from db.manager import DatabaseManager

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
logger = logging.getLogger("dashboard.api")

_db: Optional[DatabaseManager] = None


def setup_dashboard(db: DatabaseManager) -> None:
    """Inject the app-wide DatabaseManager singleton."""
    global _db
    _db = db


def _build_summary(db: DatabaseManager) -> dict:
    """Synchronous helper — runs in threadpool."""
    with db._connect() as conn:
        # ── Devices ──────────────────────────────────────
        row = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN status='active'  THEN 1 ELSE 0 END) AS up,"
            " SUM(CASE WHEN status='down'    THEN 1 ELSE 0 END) AS down,"
            " SUM(CASE WHEN status NOT IN ('active','down') THEN 1 ELSE 0 END) AS unknown"
            " FROM devices"
        ).fetchone()
        devices = {
            "total": row[0] or 0,
            "up": row[1] or 0,
            "down": row[2] or 0,
            "unknown": row[3] or 0,
        }

        # ── Interfaces ───────────────────────────────────
        row = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN status='up'   THEN 1 ELSE 0 END) AS up,"
            " SUM(CASE WHEN status='down' THEN 1 ELSE 0 END) AS down"
            " FROM interfaces"
        ).fetchone()
        interfaces = {
            "total": row[0] or 0,
            "up": row[1] or 0,
            "down": row[2] or 0,
        }

        # ── Security ─────────────────────────────────────
        row = conn.execute(
            "SELECT AVG(score), COUNT(*) FROM security_audits"
        ).fetchone()
        avg_score = round(row[0], 1) if row[0] is not None else 0.0
        total_audits = row[1] or 0

        row = conn.execute(
            "SELECT COUNT(*) FROM security_events"
            " WHERE severity IN ('critical','high')"
            " AND created_at >= datetime('now','-24 hours')"
        ).fetchone()
        critical_events = row[0] or 0

        security = {
            "avg_score": avg_score,
            "total_audits": total_audits,
            "critical_events": critical_events,
        }

        # ── Drift ────────────────────────────────────────
        row = conn.execute(
            "SELECT COUNT(*),"
            " SUM(CASE WHEN has_drift=1 THEN 1 ELSE 0 END)"
            " FROM drift_events"
            " WHERE id IN (SELECT MAX(id) FROM drift_events GROUP BY device_id)"
        ).fetchone()
        total_checked = row[0] or 0
        drifted = row[1] or 0
        compliance_pct = round(
            ((total_checked - drifted) / total_checked * 100) if total_checked > 0 else 100.0, 1
        )
        drift = {
            "total_events": total_checked,
            "devices_with_drift": drifted,
            "compliance_pct": compliance_pct,
        }

        # ── Deployments ──────────────────────────────────
        row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        total_jobs = row[0] or 0

        row = conn.execute(
            "SELECT COUNT(*) FROM jobs"
            " WHERE created_at >= datetime('now','-7 days')"
        ).fetchone()
        recent_7d = row[0] or 0

        row = conn.execute(
            "SELECT status FROM jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_status = row[0] if row else None

        deployments = {
            "total_jobs": total_jobs,
            "recent_7d": recent_7d,
            "last_status": last_status,
        }

        # ── Recent Security Events ───────────────────────
        rows = conn.execute(
            "SELECT id, event_type, severity, description, created_at"
            " FROM security_events"
            " ORDER BY id DESC LIMIT 10"
        ).fetchall()
        recent_events = [
            {
                "id": r[0], "event_type": r[1], "severity": r[2],
                "description": r[3], "created_at": r[4],
            }
            for r in rows
        ]

        # ── Recent Audit Log ─────────────────────────────
        rows = conn.execute(
            "SELECT id, action, target, details, created_at"
            " FROM audit_log"
            " ORDER BY id DESC LIMIT 10"
        ).fetchall()
        recent_audit_log = [
            {
                "id": r[0], "action": r[1], "target": r[2],
                "details": r[3], "created_at": r[4],
            }
            for r in rows
        ]

        return {
            "devices": devices,
            "interfaces": interfaces,
            "security": security,
            "drift": drift,
            "deployments": deployments,
            "recent_events": recent_events,
            "recent_audit_log": recent_audit_log,
        }


@router.get("/summary")
async def get_dashboard_summary():
    """
    One-shot aggregate endpoint for the NMC landing page.

    Returns device counts, interface counts, security score,
    drift compliance %, recent deployments, events, and audit log.
    """
    if _db is None:
        return {"error": "Dashboard not initialized"}
    return await run_in_threadpool(_build_summary, _db)
