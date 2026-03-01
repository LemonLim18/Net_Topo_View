from fastapi import APIRouter, HTTPException, BackgroundTasks
from starlette.concurrency import run_in_threadpool
from typing import List, Optional
from datetime import datetime
import logging

from db.manager import DatabaseManager
from drift.comparator import DriftComparator
from drift.models import DriftEvent

logger = logging.getLogger("drift.api")

DB_PATH = "db/netauto.db"

router = APIRouter()


@router.get("/drift/events")
async def list_drift_events(
    limit: int = 50,
    device_id: Optional[int] = None,
):
    """Return recent drift events (newest first)."""
    db = DatabaseManager(DB_PATH)
    rows = await run_in_threadpool(db.get_drift_events, limit=limit, device_id=device_id)
    return {"events": rows}


@router.post("/drift/check/{hostname}", response_model=DriftEvent)
async def check_drift_for_device(hostname: str):
    """
    Triggers an immediate drift check for a specific device.
    """
    db = DatabaseManager(DB_PATH)
    comparator = DriftComparator(db)

    event = await run_in_threadpool(comparator.check_drift, hostname)

    if not event:
        raise HTTPException(status_code=404, detail=f"Device {hostname} not found or no config history")

    await run_in_threadpool(db.save_drift_event, event)
    return event


@router.post("/drift/check/all")
async def check_drift_all(background_tasks: BackgroundTasks):
    """
    Triggers a network-wide drift check in the background.
    """
    background_tasks.add_task(run_network_drift_check)
    return {"status": "started", "message": "Network-wide drift check running in background"}


def run_network_drift_check():
    db = DatabaseManager(DB_PATH)
    comparator = DriftComparator(db)
    devices = db.get_all_devices()

    results = []
    for dev in devices:
        try:
            event = comparator.check_drift(dev.hostname)
            if event:
                if event.has_drift:
                    db.save_drift_event(event)
                results.append(event)
        except Exception as e:
            logger.warning("Drift check failed for %s: %s", dev.hostname, e)

    logger.info("Network drift check complete: %d devices checked, %d with drift",
                len(results), sum(1 for e in results if e.has_drift))
    return results


@router.get("/drift/summary")
async def drift_summary():
    """
    Phase 13 — Drift KPI summary for dashboard.
    Returns compliance rate, device counts, and latest drift timestamps.
    """
    db = DatabaseManager(DB_PATH)
    rows = await run_in_threadpool(db.get_drift_events, limit=500)
    events = rows if isinstance(rows, list) else []

    # De-duplicate: keep latest per device
    latest_by_device: dict = {}
    for ev in events:
        dev_id = ev.get("device_id") or ev.get("hostname", "unknown")
        existing = latest_by_device.get(dev_id)
        if existing is None or (ev.get("timestamp", "") > existing.get("timestamp", "")):
            latest_by_device[dev_id] = ev

    total = len(latest_by_device)
    drifted = sum(1 for e in latest_by_device.values() if e.get("has_drift"))
    compliant = total - drifted
    compliance_pct = round((compliant / total) * 100, 1) if total > 0 else 100.0

    severity_counts = {"low": 0, "medium": 0, "high": 0}
    for e in latest_by_device.values():
        sev = (e.get("severity") or "low").lower()
        if sev in severity_counts:
            severity_counts[sev] += 1

    return {
        "total_devices": total,
        "compliant": compliant,
        "drifted": drifted,
        "compliance_pct": compliance_pct,
        "severity_breakdown": severity_counts,
    }
