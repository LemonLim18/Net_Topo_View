
# backend.py
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# --- Core Modules ---
from db.manager import DatabaseManager
from deploy.pipeline import DeploymentPipeline
from llm.agent import NetworkAgent
from llm.refresh_fabric import register_refresh_listener, register_refresh_sink
from llm.refresh_state import get_refresh_state_manager

# --- API Routers ---
from crawler.api import router as crawler_router, set_crawl_runner
from deploy.api import router as deploy_router, setup_pipeline
from llm.api import router as llm_router, setup_agent
from monitor.api import router as monitor_router
from netbox.api import router as netbox_api_router
from network.api import router as network_router, set_db_manager as set_network_db
from netbox.sync_devices import DeviceSyncer
from netbox.sync_topology import TopologySyncer
from netbox.webhook import router as netbox_webhook_router
from drift.api import router as drift_router
from compliance.api import router as compliance_router
from config_engine.api import router as config_router
from security.api import router as security_router
from auth.api import router as auth_router
from auth.auth import seed_default_admin
from scheduler.core import start_scheduler, stop_scheduler
from ssh_terminal.api import router as ssh_router
from ssh_terminal.session import cleanup_idle_sessions
from llm.embedder import GeminiEmbedder
from llm.vector_store import KnowledgeVectorStore
from llm.retriever import KnowledgeRetriever
from llm.ingestion import DocumentIngester
from rag.api import router as rag_router, setup_rag, setup_context_bundle
from dashboard.api import router as dashboard_router, setup_dashboard

# M22 — Ansible-first automation (optional: graceful no-op if ansible-runner absent)
try:
    from ansible.api import router as ansible_router, setup_ansible
    from ansible.builder import PlaybookBuilder
    _ANSIBLE_ENABLED = True
except ImportError:
    ansible_router = None   # type: ignore[assignment]
    _ANSIBLE_ENABLED = False

# M25 — Phase-2 Autonomous Agent APIs
from incidents.api import router as incidents_router, setup_incidents
from agent.api import router as agent_router, setup_agent_api

# M0 — Device Onboarding Wizard
from onboarding.api import router as onboarding_router

# Phase 1 — Intent Lifecycle Management (UC02-UC05)
from llm.intent_api import router as intent_router, setup_intents

# Phase 3 — SDN Controller Management
from sdn.api import router as sdn_router, setup_sdn

# Phase 5 — Threshold-Based Telemetry Monitoring
from monitor.threshold_engine import router as threshold_router, setup_thresholds

# Phase 7 — Unified Validator Report
from config_engine.unified_validator import router as validator_router, setup_validator

# M4 — Deployment Approval Gate
try:
    from deploy.approval import router as approval_router, set_db_manager as _set_approval_db
    _APPROVAL_ENABLED = True
except ImportError:
    approval_router = None  # type: ignore[assignment]
    _APPROVAL_ENABLED = False

# --- Crawler Logic (refactored BFS engine — replaces legacy mini_nce) ---
from crawler.bfs_engine import crawl_topology as _bfs_crawl
from crawler.store import save_crawl_result

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
CRAWL_WORKERS = int(os.getenv("CRAWL_WORKERS", "6"))

# Seeds & credentials from environment (no hardcoded secrets)
CRAWL_SEEDS: List[str] = [
    s.strip() for s in os.getenv("CRAWL_SEEDS", "192.168.1.123").split(",") if s.strip()
]
CRAWL_USER     = os.getenv("CRAWL_USER", "")
CRAWL_PASSWORD = os.getenv("CRAWL_PASSWORD", "")
CRAWL_KEYFILE  = os.getenv("CRAWL_KEYFILE") or None
CRAWL_DRIVER   = os.getenv("CRAWL_DRIVER", "cisco_ios")
CRAWL_JUMP     = None  # set via env JSON if needed

# Per-device credential overrides: "IP:user:pass,IP:user:pass,..."
_CRAWL_CRED_OVERRIDES: Dict[str, Tuple[str, str]] = {}
_raw_overrides = os.getenv("CRAWL_CRED_OVERRIDES", "")
for _entry in _raw_overrides.split(","):
    _entry = _entry.strip()
    if _entry.count(":") >= 2:
        _parts = _entry.split(":", 2)  # IP:user:password (password may contain ':')
        _CRAWL_CRED_OVERRIDES[_parts[0].strip()] = (_parts[1].strip(), _parts[2].strip())

# Global State
_current_topology_dict: Optional[dict] = None
_current_topology_json: Optional[str] = None
_previous_topology_dict: Optional[dict] = None
_topology_lock = asyncio.Lock()

# Shared instances promoted to module scope so run_crawl_and_broadcast
# can feed every fresh crawl result into the agent's RAG cache.
_db: Optional[DatabaseManager] = None
_agent: Optional[NetworkAgent] = None
_vault = None  # CredentialVault — set in lifespan
_state_embedder = None  # NetworkStateEmbedder — Phase 9

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
    """Run BFS topology discovery using env-configured seeds & credentials.

    Falls back to CredentialVault (M15) when CRAWL_USER is empty and the
    vault has a 'default' SSH credential stored.
    """
    user = CRAWL_USER
    password = CRAWL_PASSWORD

    # If env creds are blank, try the CredentialVault for device_id=0 (global default)
    if not user and _vault is not None:
        cred = _vault.get(device_id=0, label="SSH")
        if cred:
            user = cred["username"]
            password = cred["password"]
            logger.info("Using CredentialVault default SSH credential for crawl")

    if not user:
        logger.error("No crawl credentials configured — set CRAWL_USER/CRAWL_PASSWORD or store a vault entry")
        return {"timestamp": datetime.now().isoformat(), "seeds": CRAWL_SEEDS, "nodes": {}}

    return _bfs_crawl(
        seeds=CRAWL_SEEDS,
        username=user,
        password=password,
        key_file=CRAWL_KEYFILE,
        driver_type=CRAWL_DRIVER,
        jump_host=CRAWL_JUMP,
        workers=CRAWL_WORKERS,
        cred_overrides=_CRAWL_CRED_OVERRIDES or None,
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
    refresh_state = get_refresh_state_manager()
    refresh_state.record_start("live_topology", trigger=trigger)
    loop = asyncio.get_running_loop()
    try:
        new_topo = await loop.run_in_executor(None, do_sync_crawl)
    except Exception as e:
        logger.exception(f"Crawl failed: {e}")
        refresh_state.record_failure(
            "live_topology",
            str(e),
            result={"status": "failed", "trigger": trigger},
            trigger=trigger,
        )
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

        # 2b. Keep the agent's RAG cache in sync with every crawl result
        if _db is not None:
            _db.update_topology(new_topo)
            # Persist devices + links to SQL (enriched with model/version/serial)
            try:
                save_crawl_result(_db, new_topo, triggered_by=trigger)
            except Exception as e:
                logger.warning("save_crawl_result failed: %s", e)
        if _agent is not None:
            _agent.refresh_context()
            logger.debug("Agent RAG context refreshed after crawl")

        # 2c. Phase 9 — Auto-embed network state into ChromaDB
        if _state_embedder is not None:
            try:
                embed_result = await loop.run_in_executor(
                    None, _state_embedder.embed_crawl_result, new_topo, _previous_topology_dict
                )
                logger.info("Auto-embedded network state: %s", embed_result.get("status"))
            except Exception as e:
                logger.warning("Network state auto-embedding failed: %s", e)

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

    refresh_state.record_success(
        "live_topology",
        result={
            "status": "success",
            "trigger": trigger,
            "timestamp": new_topo.get("timestamp"),
            "node_count": len(new_topo.get("nodes", {})),
            "topology_changed": topology_changed,
        },
        trigger=trigger,
    )

# (periodic_crawler removed — now managed by M16 scheduler)

# ----------------------
# Lifespan & App Setup
# ----------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize Database
    db = DatabaseManager("db/netauto.db")
    db.initialize()
    logger.info("Database initialized")

    # 1b. Seed default admin user (M15)
    seed_default_admin()

    # 2. Initialize Core Systems
    pipeline = DeploymentPipeline(db)

    # M21 — Vector-Embedded RAG stack
    embedder = GeminiEmbedder(api_key=os.getenv("GEMINI_API_KEY"))
    vector_store = KnowledgeVectorStore(embedder)
    retriever = KnowledgeRetriever(vector_store, embedder)
    ingester = DocumentIngester(vector_store)
    logger.info("Vector knowledge store ready (chunks=%d)", vector_store.count())
    setup_rag(ingester, vector_store)

    # Phase 9 — Auto Network State Embedding
    global _state_embedder
    try:
        from rag.auto_embedder import NetworkStateEmbedder
        from rag.api import setup_state_embedder
        _state_embedder = NetworkStateEmbedder(vector_store, embedder, db)
        setup_state_embedder(_state_embedder)
        register_refresh_listener("rag_state_embedder", _state_embedder.handle_refresh_event)
        logger.info("Network state auto-embedder initialized (Phase 9)")
    except Exception as exc:
        logger.warning("Network state auto-embedder init failed: %s", exc)
        _state_embedder = None

    from llm.provider import get_llm_provider

    _main_llm = get_llm_provider()
    agent = NetworkAgent(db, retriever=retriever, provider=_main_llm)
    refresh_state = get_refresh_state_manager()
    refresh_state.register_handler(
        "live_topology",
        lambda: run_crawl_and_broadcast(trigger="tool_manual"),
    )
    refresh_state.register_handler(
        "inventory",
        lambda: DeviceSyncer(db).sync_all_devices(),
    )
    refresh_state.register_handler(
        "planned_topology",
        lambda: TopologySyncer(db).sync_connections(),
    )

    # 3. Initialize Credential Vault (M15)
    from auth.credentials import CredentialVault
    vault = CredentialVault()

    # 4. Setup Dependencies
    setup_pipeline(db)           # Inject global pipeline
    setup_agent(agent, pipeline=pipeline, vault=vault)  # Inject agent + M4/M15
    setup_dashboard(db)            # Inject DB for dashboard summary
    set_crawl_runner(run_crawl_and_broadcast) # Inject crawl runner to API

    # M4 — Inject DB into approval module so its Depends(_get_db) works at request time
    if _APPROVAL_ENABLED:
        _set_approval_db(db)
        logger.info("Deployment approval gate initialized")

    # M22 — Ansible-first orchestration layer
    _builder = None
    if _ANSIBLE_ENABLED:
        _builder = PlaybookBuilder(_main_llm)
        setup_ansible(_builder, agent, pipeline, db)
        logger.info("Ansible orchestration layer initialized (M22)")

    # M25 — Phase-2: Agent Plan/Execute and Incidents
    setup_agent_api(agent, builder=_builder, db=db)
    logger.info("Autonomous Agent Plan/Execute API initialized (M25)")

    # Phase 1 — Intent Lifecycle Management
    setup_intents(db)
    logger.info("Intent Lifecycle API initialized")

    # Phase 2 — RAG Context Bundle (UC06-UC09)
    setup_context_bundle(db)
    logger.info("RAG Context Bundle builder initialized")

    # Phase 3 — SDN Controller Management
    setup_sdn(db)
    logger.info("SDN Controller Management initialized")

    # Phase 5 — Threshold-Based Telemetry Monitoring
    setup_thresholds(db)
    logger.info("Threshold monitoring engine initialized")

    # Phase 7 — Unified Validator Report
    setup_validator(db)
    logger.info("Unified validator initialized")

    # Config Backup — sharing the DB manager (for live topology status)
    set_network_db(db)

    # M25 — Incident Response Orchestrator (closed-loop pipeline)
    from incidents.orchestrator import IncidentOrchestrator
    _orchestrator = IncidentOrchestrator(
        db=db,
        agent=agent,
        builder=_builder,
        broadcast_fn=_telemetry_broadcast,
    )
    setup_incidents(_orchestrator)
    logger.info("Incident Response Orchestrator initialized (M25)")

    # 4. Load Saved Topology
    global _current_topology_dict, _current_topology_json, _previous_topology_dict, _db, _agent, _vault
    _db = db
    _agent = agent
    _vault = vault
    register_refresh_sink("sqlite_context_event_log", db.append_context_event_log)
    try:
        os.makedirs(TOPOLOGY_SAVE_DIR, exist_ok=True)
        if os.path.exists(CURRENT_TOPOLOGY_FILE):
             with open(CURRENT_TOPOLOGY_FILE, 'r') as f:
                _current_topology_dict = json.load(f)
                _current_topology_json = json.dumps(_current_topology_dict, sort_keys=True)
                # Seed the agent's RAG cache immediately on startup so it has
                # network knowledge before the first scheduled crawl fires.
                db.update_topology(_current_topology_dict)
                agent.refresh_context()
                logger.info("Agent RAG context seeded from saved topology (%d nodes)",
                            len(_current_topology_dict.get("nodes", {})))
        if os.path.exists(PREVIOUS_TOPOLOGY_FILE):
             with open(PREVIOUS_TOPOLOGY_FILE, 'r') as f:
                _previous_topology_dict = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load initial topology: {e}")

    # 5. Start M16 Scheduler (replaces old periodic_crawler loop)
    start_scheduler(run_crawl_and_broadcast, orchestrator=_orchestrator, broadcast_fn=_telemetry_broadcast)
    logger.info("Background scheduler started")

    # 6. SSH idle-session reaper (every 2 minutes)
    async def _ssh_reaper():
        while True:
            await asyncio.sleep(120)
            reaped = cleanup_idle_sessions()
            if reaped:
                logger.info("SSH reaper closed %d idle session(s)", len(reaped))

    reaper_task = asyncio.create_task(_ssh_reaper())
    logger.info("SSH idle-session reaper started (interval=120s, timeout=600s)")

    yield

    # Cleanup
    reaper_task.cancel()
    stop_scheduler()

app = FastAPI(title="CogniShield Backend", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS — belt-and-suspenders approach
# ---------------------------------------------------------------------------
# 1) Standard CORSMiddleware for normal responses
# 2) A catch-all middleware that guarantees CORS headers even on unhandled
#    500 errors (which can bypass CORSMiddleware in some Starlette versions).

_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse as StarletteJSONResponse, Response as StarletteResponse
from starlette.middleware.base import BaseHTTPMiddleware

class CORSErrorCatchMiddleware(BaseHTTPMiddleware):
    """Catches unhandled exceptions and returns a JSON 500 with CORS headers.

    This sits *outside* CORSMiddleware in the stack so even if Starlette's
    ServerErrorMiddleware intercepts the exception first, we still inject
    the correct Access-Control-Allow-Origin header.
    """
    async def dispatch(self, request: StarletteRequest, call_next):
        origin = request.headers.get("origin", "")
        try:
            response: StarletteResponse = await call_next(request)
        except Exception as exc:
            logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
            response = StarletteJSONResponse(
                status_code=500,
                content={"detail": str(exc) or "Internal Server Error"},
            )
        # Guarantee CORS header on every response (including errors)
        if origin in _ALLOWED_ORIGINS and "access-control-allow-origin" not in response.headers:
            response.headers["access-control-allow-origin"] = origin
            response.headers["access-control-allow-credentials"] = "true"
        return response

app.add_middleware(CORSErrorCatchMiddleware)

# Mount Routers
app.include_router(crawler_router, prefix="/api/v1")
app.include_router(deploy_router, prefix="/api/v1")
app.include_router(llm_router, prefix="/api/v1")
app.include_router(monitor_router, prefix="/api/v1")
app.include_router(netbox_api_router, prefix="/api/v1/netbox", tags=["NetBox"])
app.include_router(netbox_webhook_router, prefix="/api/v1/netbox", tags=["NetBox"])
app.include_router(network_router, prefix="/api/v1", tags=["Network"])
app.include_router(drift_router, prefix="/api/v1", tags=["Drift"])
app.include_router(compliance_router, prefix="/api/v1", tags=["Compliance"])
app.include_router(config_router, prefix="/api/v1", tags=["Config Engine"])
app.include_router(security_router, prefix="/api/v1", tags=["Security"])
app.include_router(auth_router, prefix="/api/v1", tags=["Authentication"])
app.include_router(ssh_router, prefix="/api/v1", tags=["SSH Terminal"])
app.include_router(rag_router, prefix="/api/v1", tags=["RAG Knowledge Base"])
app.include_router(dashboard_router, prefix="/api/v1", tags=["Dashboard"])
if _ANSIBLE_ENABLED and ansible_router is not None:
    app.include_router(ansible_router, prefix="/api/v1", tags=["Ansible Orchestration (M22)"])

# M4 — Deployment approval gate
if _APPROVAL_ENABLED and approval_router is not None:
    app.include_router(approval_router, tags=["Deploy Approval (M4)"])

# M25 — Phase-2 Autonomous Agent APIs
app.include_router(incidents_router, prefix="/api/v1", tags=["Incidents (M25)"])
app.include_router(agent_router, prefix="/api/v1", tags=["Autonomous Agent (M25)"])

# M0 — Onboarding
app.include_router(onboarding_router, prefix="/api/v1", tags=["Onboarding (M0)"])

# Phase 1 — Intent Lifecycle
app.include_router(intent_router, prefix="/api/v1", tags=["Intent Lifecycle"])

# Phase 3 — SDN Controller Management
app.include_router(sdn_router, prefix="/api/v1", tags=["SDN Controllers"])

# Phase 5 — Threshold Monitoring
app.include_router(threshold_router, prefix="/api/v1", tags=["Threshold Monitoring"])

# Phase 7 — Unified Validator
app.include_router(validator_router, prefix="/api/v1", tags=["Unified Validation"])

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


# ---------------------------------------------------------------------------
# M25: Real-time telemetry WebSocket  (WS /api/telemetry)
# ---------------------------------------------------------------------------
# Broadcasts a periodic metrics snapshot to all connected telemetry clients.
# The anomaly detector and security threat module feed events here so the
# frontend SecurityAnalysis view can display live anomaly alerts.

_telemetry_clients: Set[WebSocket] = set()
_telemetry_lock = asyncio.Lock()


async def _telemetry_broadcast(payload: dict):
    """Broadcast JSON payload to all connected telemetry WebSocket clients."""
    async with _telemetry_lock:
        clients = list(_telemetry_clients)
    if not clients:
        return
    msg = json.dumps(payload, sort_keys=True)
    await asyncio.gather(
        *[_safe_telemetry_send(ws, msg) for ws in clients],
        return_exceptions=True,
    )


async def _safe_telemetry_send(ws: WebSocket, msg: str):
    try:
        await ws.send_text(msg)
    except Exception:
        async with _telemetry_lock:
            _telemetry_clients.discard(ws)


@app.websocket("/api/telemetry")
async def telemetry_ws(websocket: WebSocket):
    """
    Real-time telemetry stream (WS /api/telemetry).

    On connect the server sends a welcome with current device count.
    Every 10 seconds it broadcasts a lightweight metrics snapshot:
      {type: "metrics", devices: [...], timestamp: "..."}

    Security events / anomaly alerts are pushed immediately as:
      {type: "anomaly", ...}  or  {type: "security_event", ...}
    """
    await websocket.accept()
    async with _telemetry_lock:
        _telemetry_clients.add(websocket)
    logger.info("Telemetry client connected; total=%d", len(_telemetry_clients))

    try:
        # Send welcome
        device_count = 0
        if _db is not None:
            try:
                with _db._connect() as conn:
                    row = conn.execute("SELECT COUNT(*) as n FROM devices").fetchone()
                    device_count = row["n"] if row else 0
            except Exception:
                pass

        await websocket.send_text(json.dumps({
            "type":         "connected",
            "device_count": device_count,
            "message":      "Telemetry stream active",
        }))

        # Periodic metrics loop
        tick = 0
        while True:
            try:
                # Short timeout so we can push proactive events quickly
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                if msg == "ping":
                    await websocket.send_text('{"type":"pong"}')
            except asyncio.TimeoutError:
                # Push periodic snapshot
                tick += 1
                snapshot: dict = {"type": "metrics", "tick": tick,
                                  "timestamp": datetime.utcnow().isoformat()}

                if _db is not None:
                    try:
                        with _db._connect() as conn:
                            devs = conn.execute(
                                "SELECT hostname, status, last_polled FROM devices LIMIT 30"
                            ).fetchall()
                            snapshot["devices"] = [dict(d) for d in devs]

                            # Latest security events (last 5 minutes)
                            evts = conn.execute(
                                "SELECT event_type, severity, source_ip, description, created_at "
                                "FROM security_events "
                                "WHERE created_at > datetime('now','-5 minutes') "
                                "ORDER BY created_at DESC LIMIT 10"
                            ).fetchall()
                            snapshot["recent_security_events"] = [dict(e) for e in evts]

                            # Open incidents count
                            inc_row = conn.execute(
                                "SELECT COUNT(*) as n FROM incidents WHERE status='open'"
                            ).fetchone()
                            snapshot["open_incidents"] = inc_row["n"] if inc_row else 0
                    except Exception as exc:
                        logger.debug("Telemetry snapshot error: %s", exc)

                await websocket.send_text(json.dumps(snapshot, default=str))

            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        async with _telemetry_lock:
            _telemetry_clients.discard(websocket)
        logger.info("Telemetry client disconnected; total=%d", len(_telemetry_clients))
