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
    set_crawl_function,
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


def start_scheduler(crawl_fn: Callable[..., Awaitable]) -> None:
    """
    Create, configure, and start the global scheduler.

    Args:
        crawl_fn: The async crawl-and-broadcast function from py
                  (injected to avoid circular imports).
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already running — skipping start")
        return

    # Inject the crawl function into jobs module
    set_crawl_function(crawl_fn)

    # Read configurable intervals
    crawl_min = _env_int("SCHED_CRAWL_MINUTES", 15)
    drift_min = _env_int("SCHED_DRIFT_MINUTES", 30)
    audit_hrs = _env_int("SCHED_AUDIT_HOURS", 6)
    cve_hrs   = _env_int("SCHED_CVE_HOURS", 24)

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

    _scheduler.start()
    logger.info(
        "Scheduler started with %d jobs — Crawl: %dm, Drift: %dm, Audit: %dh, CVE: %dh",
        len(_scheduler.get_jobs()), crawl_min, drift_min, audit_hrs, cve_hrs,
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
        _scheduler = None
