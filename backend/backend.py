
# backend.py
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import List, Optional, Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# --- Core Modules ---
from backend.db.manager import DatabaseManager
from backend.deploy.pipeline import DeploymentPipeline
from backend.llm.agent import NetworkAgent

# --- API Routers ---
from backend.crawler.api import router as crawler_router, set_crawl_runner
from backend.deploy.api import router as deploy_router, setup_pipeline
from backend.llm.api import router as llm_router, setup_agent

# --- Crawler Logic (Legacy/Transitional) ---
import backend.mini_nce as crawler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("topo-backend")

# ----------------------
# Configuration
# ----------------------
TOPOLOGY_SAVE_DIR = "topology_store"
CURRENT_TOPOLOGY_FILE = os.path.join(TOPOLOGY_SAVE_DIR, "current_topo.json")
PREVIOUS_TOPOLOGY_FILE = os.path.join(TOPOLOGY_SAVE_DIR, "previous_topo.json")
NEW_FETCH_FILE = os.path.join(TOPOLOGY_SAVE_DIR, "new_fetch.json")

CRAWL_INTERVAL_SECONDS = 300  # 5 minutes
CRAWL_WORKERS = 6
# Configure these via environment variables or secure vault in production
CRAWL_SEEDS: List[str] = ["192.168.1.123"] 
CRAWL_USER = "miinning"
CRAWL_PASSWORD = "Cisco123"
CRAWL_KEYFILE = None
CRAWL_JUMP = None

# Global State
_current_topology_dict: Optional[dict] = None
_current_topology_json: Optional[str] = None
_previous_topology_dict: Optional[dict] = None
_topology_lock = asyncio.Lock()

# ----------------------
# WebSocket Connection Manager
# ----------------------
class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self.lock:
            self.active.add(websocket)
        logger.info(f"Client connected; total={len(self.active)}")

    async def disconnect(self, websocket: WebSocket):
        async with self.lock:
            self.active.discard(websocket)
        logger.info(f"Client disconnected; total={len(self.active)}")

    async def broadcast(self, message: str):
        async with self.lock:
            conns = list(self.active)
        if not conns:
            return
        coros = [self._safe_send(ws, message) for ws in conns]
        await asyncio.gather(*coros, return_exceptions=True)

    async def _safe_send(self, ws: WebSocket, msg: str):
        try:
            await ws.send_text(msg)
        except Exception as e:
            logger.warning(f"send failed -> disconnecting: {e}")
            await self.disconnect(ws)

mgr = ConnectionManager()

# ----------------------
# Crawler Helpers
# ----------------------
def do_sync_crawl() -> dict:
    return crawler.crawl_topology(
        CRAWL_SEEDS, CRAWL_USER, CRAWL_PASSWORD, CRAWL_KEYFILE, CRAWL_JUMP, workers=CRAWL_WORKERS
    )

def canonical_topology(topo: dict) -> dict:
    """Normalize topology for comparison."""
    if not topo:
        return {"nodes": {}, "links": []}
    
    nodes_dict = topo.get("nodes", {})
    if not isinstance(nodes_dict, dict):
        nodes_dict = {}
    
    cleaned_nodes = {}
    for node_id, node_data in nodes_dict.items():
        if not isinstance(node_data, dict): continue
        # Remove volatile fields
        cleaned_node = {k: v for k, v in node_data.items() if k != "timestamp"}
        
        # Sort arrays
        if "connectedNode" in cleaned_node and isinstance(cleaned_node["connectedNode"], list):
            cleaned_node["connectedNode"] = sorted(cleaned_node["connectedNode"])
        
        # Consistent "down" state
        has_interfaces = cleaned_node.get("interfaceList") and any(bool(i) for i in cleaned_node.get("interfaceList", [{}]))
        has_links = cleaned_node.get("edgeLink") and bool(cleaned_node.get("edgeLink", {}))
        
        if "down" in node_data:
            cleaned_node["down"] = node_data["down"]
        elif not has_interfaces and not has_links:
            cleaned_node["down"] = True
        else:
            cleaned_node.pop("down", None)
        
        cleaned_nodes[node_id] = cleaned_node

    # Extract links
    links = []
    for node_id, node_data in cleaned_nodes.items():
        edge_links = node_data.get("edgeLink", {})
        if isinstance(edge_links, dict):
            for local_if, link_info in edge_links.items():
                if isinstance(link_info, dict):
                    remote_node = link_info.get("remote_node")
                    if remote_node:
                        links.append({
                            "src": node_id,
                            "dst": remote_node,
                            "src_interface": local_if,
                            "dst_interface": link_info.get("remote_interface"),
                            "remote_ip": link_info.get("remote_ip")
                        })

    sorted_nodes = dict(sorted(cleaned_nodes.items()))
    sorted_links = sorted(links, key=lambda x: (x.get("src", ""), x.get("dst", "")))
    return {"nodes": sorted_nodes, "links": sorted_links}

def save_topology_to_file(topo: dict, filepath: str, sort_keys: bool = False):
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(topo, f, indent=2, sort_keys=sort_keys)
        logger.info(f"Saved topology to {filepath}")
    except Exception as e:
        logger.error(f"Failed to save topology to {filepath}: {e}")

async def run_crawl_and_broadcast(trigger: str = "periodic"):
    global _current_topology_dict, _current_topology_json, _previous_topology_dict
    logger.info(f"Starting crawl (trigger={trigger})")
    loop = asyncio.get_running_loop()
    try:
        new_topo = await loop.run_in_executor(None, do_sync_crawl)
    except Exception as e:
        logger.exception(f"Crawl failed: {e}")
        return
    
    save_topology_to_file(new_topo, NEW_FETCH_FILE, sort_keys=True)

    async with _topology_lock:
        # 1. Archive current
        if _current_topology_dict:
            save_topology_to_file(_current_topology_dict, PREVIOUS_TOPOLOGY_FILE, sort_keys=True)
            _previous_topology_dict = _current_topology_dict
        else:
            _previous_topology_dict = None

        # 2. Update current
        _current_topology_dict = new_topo
        save_topology_to_file(_current_topology_dict, CURRENT_TOPOLOGY_FILE, sort_keys=True)
        _current_topology_json = json.dumps(_current_topology_dict, sort_keys=True)

        # 3. Detect changes
        previous_canon = canonical_topology(_previous_topology_dict) if _previous_topology_dict else None
        current_canon = canonical_topology(_current_topology_dict) if _current_topology_dict else canonical_topology({})

        previous_json = json.dumps(previous_canon, sort_keys=True) if previous_canon else None
        current_json = json.dumps(current_canon, sort_keys=True)

        topology_changed = (previous_json is None) or (current_json != previous_json)

        if topology_changed:
            logger.info("Topology changed. Broadcasting update.")
            event_msg = {
                "type": "topology_update",
                "timestamp": new_topo.get("timestamp"),
                "trigger": trigger,
                "full_topology": new_topo
            }
            await mgr.broadcast(json.dumps(event_msg, sort_keys=True))
        else:
            logger.info("Topology unchanged. Not broadcasting.")

async def periodic_crawler():
    while True:
        try:
            await run_crawl_and_broadcast(trigger="periodic")
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in periodic crawl")
        await asyncio.sleep(CRAWL_INTERVAL_SECONDS)

# ----------------------
# Lifespan & App Setup
# ----------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize Database
    db = DatabaseManager("net_topo.db")
    db.initialize()
    logger.info("Database initialized")

    # 2. Initialize Core Systems
    pipeline = DeploymentPipeline(db)
    agent = NetworkAgent(db)

    # 3. Setup Dependencies
    setup_pipeline(db)           # Inject global pipeline
    setup_agent(agent)           # Inject global agent
    set_crawl_runner(run_crawl_and_broadcast) # Inject crawl runner to API

    # 4. Load Saved Topology
    global _current_topology_dict, _current_topology_json, _previous_topology_dict
    try:
        os.makedirs(TOPOLOGY_SAVE_DIR, exist_ok=True)
        if os.path.exists(CURRENT_TOPOLOGY_FILE):
             with open(CURRENT_TOPOLOGY_FILE, 'r') as f:
                _current_topology_dict = json.load(f)
                _current_topology_json = json.dumps(_current_topology_dict, sort_keys=True)
        if os.path.exists(PREVIOUS_TOPOLOGY_FILE):
             with open(PREVIOUS_TOPOLOGY_FILE, 'r') as f:
                _previous_topology_dict = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load initial topology: {e}")

    # 5. Start Background Tasks
    crawl_task = asyncio.create_task(periodic_crawler())
    logger.info("Background tasks started")

    yield

    # Cleanup
    crawl_task.cancel()
    try:
        await crawl_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="CogniShield Backend", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routers
app.include_router(crawler_router, prefix="/api/v1")
app.include_router(deploy_router, prefix="/api/v1")
app.include_router(llm_router, prefix="/api/v1")

# WebSocket Endpoint (Streamlined)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await mgr.connect(websocket)
    try:
        # Send initial state
        async with _topology_lock:
            if _current_topology_json:
                await websocket.send_text(json.dumps({
                    "type": "initial_topology",
                    "topology": json.loads(_current_topology_json)
                }))
        
        # Keep alive loop
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                # Handle pings if needed
                if "ping" in msg:
                     await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await mgr.disconnect(websocket)

@app.get("/")
async def root():
    return {"status": "ok", "message": "CogniShield Backend is running"}
