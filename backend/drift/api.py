from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List, Optional
from datetime import datetime

from backend.db.manager import DatabaseManager
from backend.drift.comparator import DriftComparator
from backend.drift.models import DriftEvent

router = APIRouter()

@router.post("/drift/check/{hostname}", response_model=DriftEvent)
async def check_drift_for_device(hostname: str):
    """
    Triggers an immediate drift check for a specific device.
    """
    db = DatabaseManager("net_topo.db")
    comparator = DriftComparator(db)
    
    event = comparator.check_drift(hostname)
    
    if not event:
        raise HTTPException(status_code=404, detail=f"Device {hostname} not found or no config history")
        
    # Persist event
    db.save_drift_event(event)

    return event

@router.post("/drift/check/all")
async def check_drift_all(background_tasks: BackgroundTasks):
    """
    Triggers a network-wide drift check in the background.
    """
    background_tasks.add_task(run_network_drift_check)
    return {"status": "started", "message": "Network-wide drift check running in background"}

def run_network_drift_check():
    db = DatabaseManager("net_topo.db")
    comparator = DriftComparator(db)
    devices = db.get_all_devices()
    
    results = []
    for dev in devices:
        try:
            event = comparator.check_drift(dev.hostname)
            if event:
                # Log drift result regardless of has_drift (to show it was checked)
                # or only log active drift. Let's log active drift for now to save space.
                if event.has_drift:
                    db.save_drift_event(event)
                results.append(event)
        except Exception as e:
            pass
            
    # Logic to aggregate results would go here
