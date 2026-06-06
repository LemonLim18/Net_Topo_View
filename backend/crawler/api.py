"""
M2 Topology API — REST endpoints for the crawler.

Exposes network topology data and manual crawl triggers.
"""

import logging
import json
import os
import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

# Reuse the same constants or import them from config if available
TOPOLOGY_SAVE_DIR = "topology_store"
CURRENT_TOPOLOGY_FILE = os.path.join(TOPOLOGY_SAVE_DIR, "current_topo.json")

router = APIRouter(prefix="/topology", tags=["Topology (M2)"])
logger = logging.getLogger("crawler.api")

# We will inject the crawler runner function from the main app to avoid circular imports
_crawl_runner = None

def set_crawl_runner(runner_func):
    """Inject the crawl runner function (dependency injection)."""
    global _crawl_runner
    _crawl_runner = runner_func

@router.get("")
@router.get("/")
async def get_topology():
    """Get the current network topology from the persistent JSON store."""
    if os.path.exists(CURRENT_TOPOLOGY_FILE):
        try:
            with open(CURRENT_TOPOLOGY_FILE, 'r') as f:
                data = json.load(f)
            return data
        except Exception as e:
             logger.error(f"Failed to read topology file: {e}")
             raise HTTPException(500, "Internal error reading topology")
    
    return JSONResponse({"status": "no-data", "nodes": {}, "links": []}, status_code=200)

@router.post("/crawl")
async def trigger_crawl(background_tasks: BackgroundTasks):
    """Trigger a manual network crawl."""
    if not _crawl_runner:
        raise HTTPException(503, "Crawler service not initialized")
    
    background_tasks.add_task(_crawl_runner, trigger="manual")
    return {"status": "scheduled"}
