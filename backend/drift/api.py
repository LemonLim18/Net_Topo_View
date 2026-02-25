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
