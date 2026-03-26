"""
M16 — Scheduled background jobs.

Each function wraps an existing module entry-point so the scheduler
can call it safely (catches all exceptions, logs errors).
"""

import asyncio
import logging
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("scheduler.jobs")


# ────────────────────────────────────────────────────────
# Injected dependencies
# ────────────────────────────────────────────────────────
_crawl_fn: Optional[Callable[..., Awaitable]] = None
_orchestrator = None  # IncidentOrchestrator — set via set_orchestrator()
_broadcast_fn: Optional[Callable[..., Awaitable]] = None  # WebSocket broadcast


def set_crawl_function(fn: Callable[..., Awaitable]) -> None:
    """Inject the crawl-and-broadcast coroutine from backend.py."""
    global _crawl_fn
    _crawl_fn = fn


def set_orchestrator(orch) -> None:
    """Inject the IncidentOrchestrator instance."""
    global _orchestrator
    _orchestrator = orch


def set_broadcast_fn(fn: Callable[..., Awaitable]) -> None:
    """Inject the WebSocket broadcast function from backend.py."""
    global _broadcast_fn
    _broadcast_fn = fn


# ────────────────────────────────────────────────────────
# Job 1: Network Crawl
# ────────────────────────────────────────────────────────
async def job_crawl() -> None:
    """Periodic network crawl (calls run_crawl_and_broadcast)."""
    if _crawl_fn is None:
        logger.warning("Crawl function not set — skipping")
        return
    try:
        logger.info("[Scheduler] Crawl job triggered")
        await _crawl_fn(trigger="periodic")
    except Exception:
        logger.exception("[Scheduler] Crawl job failed")


# ────────────────────────────────────────────────────────
# Job 2: Drift Detection
# ────────────────────────────────────────────────────────
async def job_drift_check() -> None:
    """Periodic drift check across all devices, with WebSocket push."""
    try:
        logger.info("[Scheduler] Drift-check job triggered")
        from drift.api import run_network_drift_check
        results = run_network_drift_check()

        # Push drift results over WebSocket
        if _broadcast_fn is not None and results:
            drift_count = (
                sum(
                    1
                    for e in results
                    if (
                        e.get("has_drift", False)
                        if isinstance(e, dict)
                        else getattr(e, "has_drift", False)
                    )
                )
                if isinstance(results, list)
                else 0
            )
            try:
                await _broadcast_fn({
                    "event": "drift_check_complete",
                    "data": {
                        "devices_checked": len(results) if isinstance(results, list) else 0,
                        "drift_detected": drift_count,
                        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
                    },
                })
            except Exception:
                logger.debug("[Scheduler] Drift broadcast failed (non-fatal)")
    except Exception:
        logger.exception("[Scheduler] Drift-check job failed")


# ────────────────────────────────────────────────────────
# Job 3: Security Audit
# ────────────────────────────────────────────────────────
def job_security_audit() -> None:
    """Periodic CIS security audit on all devices."""
    try:
        logger.info("[Scheduler] Security-audit job triggered")
        from db.manager import DatabaseManager
        from security.engine import SecurityAuditEngine

        db = DatabaseManager("db/netauto.db")
        engine = SecurityAuditEngine()
        audits = engine.run_audit_all(db)
        logger.info("[Scheduler] Security-audit complete: %d devices audited", len(audits))
    except Exception:
        logger.exception("[Scheduler] Security-audit job failed")


# ────────────────────────────────────────────────────────
# Job 4: CVE Rescan
# ────────────────────────────────────────────────────────
def job_cve_rescan() -> None:
    """Periodic CVE correlation rescan."""
    try:
        logger.info("[Scheduler] CVE-rescan job triggered")
        from db.manager import DatabaseManager
        from security.cve.matcher import CVEMatcher

        db = DatabaseManager("db/netauto.db")
        matcher = CVEMatcher()
        results = matcher.scan_all_devices(db)
        total = sum(len(v) for v in results.values())
        logger.info("[Scheduler] CVE-rescan complete: %d CVEs across %d devices", total, len(results))
    except Exception:
        logger.exception("[Scheduler] CVE-rescan job failed")


# ────────────────────────────────────────────────────────
# Job 5: Anomaly Detection & Incident Response
# ────────────────────────────────────────────────────────
async def job_anomaly_detect() -> None:
    """
    Periodic anomaly detection:
      1. Scan recent security events
      2. Create incidents for new anomalies
      3. Run AI analysis (RCA + playbook)
      4. Broadcast to UI
    """
    if _orchestrator is None:
        logger.warning("Orchestrator not set — skipping anomaly detection")
        return
    try:
        logger.info("[Scheduler] Anomaly-detection job triggered")

        # Step 1+2: Detect & ingest
        new_ids = _orchestrator.detect_and_ingest()
        if not new_ids:
            logger.info("[Scheduler] No new incidents from anomaly scan")
            return

        logger.info("[Scheduler] Created %d new incident(s): %s", len(new_ids), new_ids)

        # Step 3+4: Analyse each and broadcast
        for inc_id in new_ids:
            try:
                await _orchestrator.analyze_incident(inc_id)
                await _orchestrator.broadcast_incident(inc_id, "incident_created")
            except Exception:
                logger.exception("[Scheduler] Failed to analyse incident %s", inc_id)

    except Exception:
        logger.exception("[Scheduler] Anomaly-detection job failed")
    except Exception:
        logger.exception("[Scheduler] CVE-rescan job failed")
