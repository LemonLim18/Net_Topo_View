from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
import os
import logging
from db.manager import DatabaseManager
from monitor.telegraf_gen import TelegrafConfigGenerator

router = APIRouter(prefix="/monitor", tags=["monitor"])
logger = logging.getLogger("api.monitor")

# Dependency injection for DatabaseManager would be better, 
# but essentially we need access to the singleton DB used by main app.
# We will instantiate a new manager or pass it if possible.
# For now, simplest is to use the singleton pattern or re-instantiate (SQLite is file based).

class StatusResponse(BaseModel):
    status: str
    message: str

@router.post("/update-config", response_model=StatusResponse)
async def update_telegraf_config():
    """Regenerate Telegraf configuration based on current inventory."""
    try:
        # Re-using the DB connection path
        db = DatabaseManager("net_topo.db") 
        gen = TelegrafConfigGenerator(db)
        
        # In a real deployment, we might write to /etc/telegraf/telegraf.conf
        # For local dev, we write to ./telegraf.conf
        gen.regenerate_config("telegraf.conf")
        
        # TODO: Reload Telegraf service
        # os.system("systemctl reload telegraf") or docker kill -s HUP telegraf
        
        return StatusResponse(status="success", message="Telegraf configuration regenerated.")
    except Exception as e:
        logger.error(f"Error updating config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status", response_model=StatusResponse)
async def get_monitor_status():
    """Check if monitoring services are reachable."""
    # Mock status for now
    return StatusResponse(status="unknown", message="InfluxDB/Telegraf checks not implemented yet.")
