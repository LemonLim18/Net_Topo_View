"""
M16 — Task Scheduler core.

Uses APScheduler's AsyncIOScheduler to manage periodic background tasks.
Intervals are configurable via environment variables.
"""

import logging
import os
from typing import Callable, Awaitable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from scheduler.jobs import (
    job_crawl,
    job_drift_check,
    job_security_audit,
    job_cve_rescan,
    job_anomaly_detect,
    set_crawl_function,
    set_orchestrator,
    set_broadcast_fn,
)

logger = logging.getLogger("scheduler.core")

# ────────────────────────────────────────────────────────
# Singleton scheduler instance
# ────────────────────────────────────────────────────────
_scheduler: AsyncIOScheduler | None = None


def _env_int(name: str, default: int) -> int:
    """Read an integer from an environment variable with a fallback."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def start_scheduler(crawl_fn: Callable[..., Awaitable], orchestrator=None, broadcast_fn=None) -> None:
    """
    Create, configure, and start the global scheduler.

    Args:
        crawl_fn:      The async crawl-and-broadcast function from backend.py.
        orchestrator:  IncidentOrchestrator instance (optional).
        broadcast_fn:  WebSocket broadcast function for push events (optional).
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already running — skipping start")
        return

    # Inject dependencies into jobs module
    set_crawl_function(crawl_fn)
    if orchestrator is not None:
        set_orchestrator(orchestrator)
    if broadcast_fn is not None:
        set_broadcast_fn(broadcast_fn)

    # Read configurable intervals
    crawl_min   = _env_int("SCHED_CRAWL_MINUTES", 15)
    drift_min   = _env_int("SCHED_DRIFT_MINUTES", 30)
    audit_hrs   = _env_int("SCHED_AUDIT_HOURS", 6)
    cve_hrs     = _env_int("SCHED_CVE_HOURS", 24)
    anomaly_min = _env_int("SCHED_ANOMALY_MINUTES", 5)

    _scheduler = AsyncIOScheduler(timezone="UTC")

    # ── Register jobs ──
    _scheduler.add_job(
        job_crawl,
        trigger=IntervalTrigger(minutes=crawl_min),
        id="network_crawl",
        name="Network Crawl",
        replace_existing=True,
    )

    _scheduler.add_job(
        job_drift_check,
        trigger=IntervalTrigger(minutes=drift_min),
        id="drift_check",
        name="Drift Detection",
        replace_existing=True,
    )

    _scheduler.add_job(
        job_security_audit,
        trigger=IntervalTrigger(hours=audit_hrs),
        id="security_audit",
        name="Security Audit",
        replace_existing=True,
    )

    _scheduler.add_job(
        job_cve_rescan,
        trigger=IntervalTrigger(hours=cve_hrs),
        id="cve_rescan",
        name="CVE Rescan",
        replace_existing=True,
    )

    _scheduler.add_job(
        job_anomaly_detect,
        trigger=IntervalTrigger(minutes=anomaly_min),
        id="anomaly_detect",
        name="Anomaly Detection & Incident Response",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started with %d jobs — Crawl: %dm, Drift: %dm, Audit: %dh, CVE: %dh, Anomaly: %dm",
        len(_scheduler.get_jobs()), crawl_min, drift_min, audit_hrs, cve_hrs, anomaly_min,
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
        _scheduler = None
