from fastapi import APIRouter, Depends, BackgroundTasks
from db.manager import DatabaseManager
from netbox.sync_devices import DeviceSyncer
from netbox.sync_topology import TopologySyncer

router = APIRouter()

def get_db():
    db = DatabaseManager("net_topo.db")
    # db.initialize() # Assumed initialized by main app
    return db

@router.post("/sync/devices")
async def sync_devices(background_tasks: BackgroundTasks):
    """
    Triggers a manual sync of all devices from NetBox.
    """
    db = DatabaseManager("net_topo.db")
    syncer = DeviceSyncer(db)
    
    # Run synchronously for MVP response, or background task
    result = syncer.sync_all_devices()
    return result

@router.post("/sync/topology")
async def sync_topology():
    """
    Triggers a manual sync of topology (cables) from NetBox.
    """
    db = DatabaseManager("net_topo.db")
    syncer = TopologySyncer(db)
    result = syncer.sync_connections()
    return result
