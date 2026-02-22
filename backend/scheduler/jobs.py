"""
M16 — Scheduled background jobs.

Each function wraps an existing module entry-point so the scheduler
can call it safely (catches all exceptions, logs errors).
"""

import logging
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("scheduler.jobs")


# ────────────────────────────────────────────────────────
# Job 1: Network Crawl
# ────────────────────────────────────────────────────────
_crawl_fn: Optional[Callable[..., Awaitable]] = None


def set_crawl_function(fn: Callable[..., Awaitable]) -> None:
    """Inject the crawl-and-broadcast coroutine from backend.py."""
    global _crawl_fn
    _crawl_fn = fn


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
def job_drift_check() -> None:
    """Periodic drift check across all devices."""
    try:
        logger.info("[Scheduler] Drift-check job triggered")
        from backend.drift.api import run_network_drift_check
        run_network_drift_check()
    except Exception:
        logger.exception("[Scheduler] Drift-check job failed")


# ────────────────────────────────────────────────────────
# Job 3: Security Audit
# ────────────────────────────────────────────────────────
def job_security_audit() -> None:
    """Periodic CIS security audit on all devices."""
    try:
        logger.info("[Scheduler] Security-audit job triggered")
        from backend.db.manager import DatabaseManager
        from backend.security.engine import SecurityAuditEngine

        db = DatabaseManager("net_topo.db")
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
        from backend.db.manager import DatabaseManager
        from backend.security.cve.matcher import CVEMatcher

        db = DatabaseManager("net_topo.db")
        matcher = CVEMatcher()
        results = matcher.scan_all_devices(db)
        total = sum(len(v) for v in results.values())
        logger.info("[Scheduler] CVE-rescan complete: %d CVEs across %d devices", total, len(results))
    except Exception:
        logger.exception("[Scheduler] CVE-rescan job failed")
