# backend.py
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import List, Optional, Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# your crawler module
import mini_nce as crawler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("topo-backend")

# ----------------------
# FastAPI app
# ----------------------
app = FastAPI(title="Topology Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # lock down in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------
# WebSocket connection manager
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
_current_topology_dict: Optional[dict] = None
_current_topology_json: Optional[str] = None
_previous_topology_dict: Optional[dict] = None
_topology_lock = asyncio.Lock()
TOPOLOGY_SAVE_DIR = "topology_store"
CURRENT_TOPOLOGY_FILE = os.path.join(TOPOLOGY_SAVE_DIR, "current_topo.json")
PREVIOUS_TOPOLOGY_FILE = os.path.join(TOPOLOGY_SAVE_DIR, "previous_topo.json")
NEW_FETCH_FILE = os.path.join(TOPOLOGY_SAVE_DIR, "new_fetch.json")

# ----------------------
# Config (can be updated in lifespan)
# ----------------------
CRAWL_INTERVAL_SECONDS = 20
CRAWL_WORKERS = 6
CRAWL_SEEDS: List[str] = []
CRAWL_USER: Optional[str] = None
CRAWL_PASSWORD: Optional[str] = None
CRAWL_KEYFILE: Optional[str] = None
CRAWL_JUMP: Optional[dict] = None

# ----------------------
# Helper to call crawler
# ----------------------
def do_sync_crawl() -> dict:
    return crawler.crawl_topology(
        CRAWL_SEEDS, CRAWL_USER, CRAWL_PASSWORD, CRAWL_KEYFILE, CRAWL_JUMP, workers=CRAWL_WORKERS
    )

def canonical_topology(topo: dict) -> dict:
    """
    Returns a normalized version of the topology:
    - Removes ephemeral fields like timestamp
    - Marks unreachable nodes as 'down'
    - Extracts and sorts links for consistent comparison
    """
    if not topo:
        return {"nodes": {}, "links": []}
    
    nodes_dict = topo.get("nodes", {})
    if not isinstance(nodes_dict, dict):
        nodes_dict = {}
    
    cleaned_nodes = {}
    for node_id, node_data in nodes_dict.items():
        if not isinstance(node_data, dict):
            continue
        cleaned_node = {k: v for k, v in node_data.items() if k != "timestamp"}
        
        # Sort connectedNode array for consistent comparison (alphabetical order)
        if "connectedNode" in cleaned_node and isinstance(cleaned_node["connectedNode"], list):
            cleaned_node["connectedNode"] = sorted(cleaned_node["connectedNode"])
        
        # Handle "down" field consistently:
        # - If "down" field exists in original, preserve it
        # - If node has no data (no interfaces and no edgeLinks), mark as down
        # - Otherwise, ensure "down" is not present (for consistent comparison)
        has_interfaces = cleaned_node.get("interfaceList") and any(bool(i) for i in cleaned_node.get("interfaceList", [{}]))
        has_links = cleaned_node.get("edgeLink") and bool(cleaned_node.get("edgeLink", {}))
        
        if "down" in node_data:
            # Preserve the original "down" field
            cleaned_node["down"] = node_data["down"]
        elif not has_interfaces and not has_links:
            # Node has no data, mark as down
            cleaned_node["down"] = True
        else:
            # Node has data, ensure "down" is not present (remove if it was added incorrectly)
            cleaned_node.pop("down", None)
        
        cleaned_nodes[node_id] = cleaned_node

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
    """Save topology to a JSON file.

    Args:
        topo: topology dict to save
        filepath: destination file path
        sort_keys: whether to dump keys in alphabetical order (useful for deterministic file contents)
    """
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
    # Save the freshly fetched topology to `new_fetch.json` first (alphabetically sorted)
    save_topology_to_file(new_topo, NEW_FETCH_FILE, sort_keys=True)

    async with _topology_lock:
        # Step 1: Copy current -> previous (persist previous if exists)
        if _current_topology_dict:
            # Persist the previous current to disk as `previous_topo.json` (sorted)
            save_topology_to_file(_current_topology_dict, PREVIOUS_TOPOLOGY_FILE, sort_keys=True)
            _previous_topology_dict = _current_topology_dict
        else:
            _previous_topology_dict = None

        # Step 2: Overwrite current with new fetch (sorted alphabetically) and persist
        _current_topology_dict = new_topo
        # Persist the canonical file on disk as `current_topo.json` in deterministic order
        save_topology_to_file(_current_topology_dict, CURRENT_TOPOLOGY_FILE, sort_keys=True)
        _current_topology_json = json.dumps(_current_topology_dict, sort_keys=True)

        # Step 3: Canonicalize current and previous for comparison
        previous_canon = canonical_topology(_previous_topology_dict) if _previous_topology_dict else None
        current_canon = canonical_topology(_current_topology_dict) if _current_topology_dict else canonical_topology({})

        previous_json = json.dumps(previous_canon, sort_keys=True) if previous_canon else None
        current_json = json.dumps(current_canon, sort_keys=True)

        # Step 4: Compare previous vs current. If previous is None (first run), treat as changed.
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
            logger.info("Broadcasted topology update to clients")
        else:
            logger.info("Topology unchanged. Not broadcasting.")

async def periodic_crawler():
    while True:
        try:
            await run_crawl_and_broadcast(trigger="periodic")
        except Exception:
            logger.exception("Error in periodic crawl")
        await asyncio.sleep(CRAWL_INTERVAL_SECONDS)

# ----------------------
# WebSocket endpoint
# ----------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Log incoming connection attempt details for debugging
    try:
        client = websocket.client
        origin = websocket.headers.get("origin") if hasattr(websocket, "headers") else None
        logger.info(f"Incoming websocket connection attempt from client={client} origin={origin}")
    except Exception:
        logger.exception("Failed to read websocket connection metadata")

    try:
        await mgr.connect(websocket)
    except Exception:
        logger.exception("Failed to accept websocket connection")
        raise
    async with _topology_lock:
        if _current_topology_json:
            initial_msg = {
                "type": "initial_topology",
                "timestamp": _current_topology_dict.get("timestamp") if _current_topology_dict else None,
                "topology": json.loads(_current_topology_json)
            }
            await websocket.send_text(json.dumps(initial_msg))
    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=300.0)
                try:
                    client_msg = json.loads(msg)
                    msg_type = client_msg.get("type", "")
                    if msg_type == "ping":
                        await websocket.send_text(json.dumps({"type": "pong", "timestamp": datetime.now().isoformat()}))
                    elif msg_type == "request_topology":
                        async with _topology_lock:
                            if _current_topology_json:
                                await websocket.send_text(json.dumps({
                                    "type": "topology_response",
                                    "topology": json.loads(_current_topology_json)
                                }))
                    else:
                        await websocket.send_text(json.dumps({"type": "ack", "msg": "ok"}))
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({"type": "ack", "msg": "ok"}))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        await mgr.disconnect(websocket)
    except Exception:
        logger.exception("Websocket endpoint error")
        await mgr.disconnect(websocket)


@app.get("/")
async def root():
                # Improved single-file UI: nicer layout, controls, pretty JSON
                html = """
<!doctype html>
<html>
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width,initial-scale=1" />
        <title>Topology Viewer</title>
        <style>
            :root{
                --bg:#0b1220; --card:#0f172a; --muted:#9aa7b6; --accent:#60a5fa; --text:#e6eef8;
            }
            html,body{height:100%;margin:0;font-family:Inter,ui-sans-serif,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:linear-gradient(180deg,#071029 0%, #081228 100%);color:var(--text)}
            .wrap{max-width:1100px;margin:28px auto;padding:20px}
            header{display:flex;align-items:center;justify-content:space-between;gap:12px}
            h1{margin:0;font-size:1.25rem}
            .meta{display:flex;gap:10px;align-items:center}
            .badge{background:rgba(255,255,255,0.04);padding:6px 10px;border-radius:8px;font-size:.9rem;color:var(--muted)}
            .controls{display:flex;gap:8px}
            button{background:var(--card);color:var(--text);border:1px solid rgba(255,255,255,0.03);padding:8px 10px;border-radius:8px;cursor:pointer}
            button.primary{background:linear-gradient(90deg,#2563eb,#60a5fa);border:none}
            .grid{display:grid;grid-template-columns:1fr 360px;gap:16px;margin-top:16px}
            .panel{background:var(--card);padding:14px;border-radius:10px}
            pre#topo{height:64vh;overflow:auto;margin:0;background:transparent;color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,monospace;font-size:13px}
            .right small{color:var(--muted);display:block;margin-bottom:8px}
            .stat{font-weight:600}
            a.link{color:var(--accent);text-decoration:underline}
            footer{margin-top:18px;color:var(--muted);font-size:0.9rem}
            /* minimal JSON syntax highlighting */
            .json-key{color:#ffd580}
            .json-string{color:#9fe6a0}
            .json-number{color:#ffb86b}
            .json-boolean{color:#ff9aa2}
            .muted{color:var(--muted)}
        </style>
    </head>
    <body>
        <div class="wrap">
            <header>
                <div>
                    <h1>Network Topology Viewer</h1>
                    <div class="muted">Live updates via WebSocket (ws://). Load over <code>http://</code>.</div>
                </div>
                <div class="meta">
                    <div class="badge" id="status">connecting…</div>
                    <div class="controls">
                        <button id="refresh">Refresh</button>
                        <button id="copy">Copy</button>
                        <button id="download">Download</button>
                        <button id="toggleRaw">Raw</button>
                    </div>
                </div>
            </header>

            <div class="grid">
                <div class="panel">
                    <pre id="topo"><code id="topo-code">Loading topology…</code></pre>
                </div>
                <div class="panel right">
                    <small>Info</small>
                    <div><span class="muted">Last update:</span> <span id="lastUpdate" class="stat">—</span></div>
                    <div><span class="muted">Nodes:</span> <span id="nodeCount" class="stat">—</span></div>
                    <div style="margin-top:10px"><small class="muted">Actions</small></div>
                    <div style="margin-top:8px"><a class="link" href="/topology" target="_blank">Open /topology (raw)</a></div>
                    <footer>Topology stored in <code>topology_store/</code></footer>
                </div>
            </div>
        </div>

        <script>
        // small helper: pretty-print + minimal syntax highlight
        function syntaxHighlight(json){
            if(typeof json !== 'string') json = JSON.stringify(json, null, 2);
            json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            return json.replace(/(\\".*?\\")(?=\s*:)/g, '<span class="json-key">$1</span>')
                                 .replace(/:\s*(\\".*?\\")/g, ': <span class="json-string">$1</span>')
                                 .replace(/:\s*(-?\d+(?:\.\d+)?)/g, ': <span class="json-number">$1</span>')
                                 .replace(/:\s*(true|false|null)/g, ': <span class="json-boolean">$1</span>');
        }

        const statusEl = document.getElementById('status');
        const topoCode = document.getElementById('topo-code');
        const lastUpdate = document.getElementById('lastUpdate');
        const nodeCount = document.getElementById('nodeCount');
        const refreshBtn = document.getElementById('refresh');
        const copyBtn = document.getElementById('copy');
        const dlBtn = document.getElementById('download');
        const toggleBtn = document.getElementById('toggleRaw');

        let lastPayload = null;
        let showRaw = false;

        async function renderPayload(payload){
            lastPayload = payload;
            lastUpdate.textContent = payload.timestamp || new Date().toISOString();
            const n = payload.nodes ? Object.keys(payload.nodes).length : (payload.nodes && typeof payload.nodes === 'object' ? Object.keys(payload.nodes).length : '—');
            nodeCount.textContent = n;
            if(showRaw){
                topoCode.textContent = JSON.stringify(payload, null, 2);
            } else {
                topoCode.innerHTML = syntaxHighlight(payload);
            }
        }

        async function fetchOnce(){
            try{
                const res = await fetch('/topology');
                if(!res.ok){ topoCode.textContent = 'No topology available yet'; statusEl.textContent = 'no-data'; return }
                const j = await res.json();
                await renderPayload(j);
                statusEl.textContent = 'loaded (static)';
            }catch(e){ topoCode.textContent = 'Failed to fetch topology: '+e; statusEl.textContent = 'fetch-error'; }
        }

        // simple polling fallback
        let pollInterval = null;
        function startPolling(){ if(pollInterval) return; pollInterval = setInterval(fetchOnce, 5000); }
        function stopPolling(){ if(pollInterval){ clearInterval(pollInterval); pollInterval = null } }

        function connectWS(){
            // per user request use plain ws:// — ensure page loaded via http://
            const proto = 'ws';
            let ws;
            try{ ws = new WebSocket(`${proto}://${location.host}/ws`); } catch(e){ statusEl.textContent = 'ws-failed'; startPolling(); return }
            ws.onopen = ()=>{ statusEl.textContent = 'ws connected'; stopPolling(); };
            ws.onclose = ()=>{ statusEl.textContent = 'ws closed'; startPolling(); setTimeout(connectWS,5000); };
            ws.onerror = (e)=>{ statusEl.textContent = 'ws error'; console.error(e); startPolling(); };
            ws.onmessage = (ev)=>{
                try{
                    const msg = JSON.parse(ev.data);
                    const payload = msg.full_topology || msg.topology || msg.initial_topology || msg;
                    renderPayload(payload);
                    statusEl.textContent = `last update: ${msg.timestamp || payload.timestamp || new Date().toISOString()}`;
                }catch(e){ console.error('malformed ws msg', e); }
            };
        }

        refreshBtn.onclick = ()=>fetchOnce();
        copyBtn.onclick = async ()=>{ if(!lastPayload) return; await navigator.clipboard.writeText(JSON.stringify(lastPayload, null, 2)); };
        dlBtn.onclick = ()=>{ if(!lastPayload) return; const blob = new Blob([JSON.stringify(lastPayload, null, 2)],{type:'application/json'}); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'topology.json'; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url); };
        toggleBtn.onclick = ()=>{ showRaw = !showRaw; toggleBtn.textContent = showRaw ? 'Pretty' : 'Raw'; if(lastPayload) renderPayload(lastPayload); };

        (async ()=>{ await fetchOnce(); connectWS(); setTimeout(()=>{ if(statusEl.textContent === 'connecting…') startPolling(); }, 2000); })();
        </script>
    </body>
</html>
"""
                return HTMLResponse(html)

# ----------------------
# HTTP endpoints
# ----------------------
@app.post("/crawl")
async def trigger_crawl(background_tasks: BackgroundTasks, req: Request):
    background_tasks.add_task(run_crawl_and_broadcast, "manual")
    return JSONResponse({"status": "scheduled"})

@app.get("/topology")
async def get_topology():
    async with _topology_lock:
        if _current_topology_json:
            return JSONResponse(json.loads(_current_topology_json))
        return JSONResponse({"status": "no-data"}, status_code=404)

# ----------------------
# Lifespan event instead of deprecated startup
# ----------------------
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    global CRAWL_SEEDS, CRAWL_USER, CRAWL_PASSWORD, CRAWL_KEYFILE, CRAWL_JUMP
    global _current_topology_dict, _current_topology_json, _previous_topology_dict
    
    # Example: hard-coded for now
    CRAWL_SEEDS = ["192.168.1.123"]
    CRAWL_USER = "miinning"
    CRAWL_PASSWORD = "Cisco123"  # don't hard-code in prod!
    CRAWL_KEYFILE = None
    CRAWL_JUMP = None

    # First-run initialization: clear saved topology files once on first start
    try:
        os.makedirs(TOPOLOGY_SAVE_DIR, exist_ok=True)
        init_marker = os.path.join(TOPOLOGY_SAVE_DIR, ".initialized")
        if not os.path.exists(init_marker):
            # Remove any existing saved topology files on first start
            for f in (CURRENT_TOPOLOGY_FILE, PREVIOUS_TOPOLOGY_FILE):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                        logger.info(f"Cleared saved topology file on first start: {f}")
                except Exception as e:
                    logger.warning(f"Failed to remove {f} on first start: {e}")
            # create marker so this runs only once
            try:
                with open(init_marker, 'w') as m:
                    m.write(datetime.utcnow().isoformat())
                logger.info("Initialization marker created; first-start cleanup complete")
            except Exception:
                logger.exception("Failed to create initialization marker")
    except Exception:
        logger.exception("Failed during first-start initialization")

    # Try to load previous topology from file if it exists
    if os.path.exists(CURRENT_TOPOLOGY_FILE):
        try:
            with open(CURRENT_TOPOLOGY_FILE, 'r') as f:
                _current_topology_dict = json.load(f)
                _current_topology_json = json.dumps(_current_topology_dict, sort_keys=True)
            logger.info(f"Loaded current topology from {CURRENT_TOPOLOGY_FILE}")
        except Exception as e:
            logger.warning(f"Failed to load current topology from file: {e}")
    
    if os.path.exists(PREVIOUS_TOPOLOGY_FILE):
        try:
            with open(PREVIOUS_TOPOLOGY_FILE, 'r') as f:
                _previous_topology_dict = json.load(f)
            logger.info(f"Loaded previous topology from {PREVIOUS_TOPOLOGY_FILE}")
        except Exception as e:
            logger.warning(f"Failed to load previous topology from file: {e}")

    # start background periodic crawler
    task = asyncio.create_task(periodic_crawler())
    logger.info("Periodic crawler started")

    yield

    # cleanup if app shuts down
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Periodic crawler stopped")

app.router.lifespan_context = lifespan

# ----------------------
# Run server
# ----------------------
# Commented out so you can run via: uvicorn backend:app --host 0.0.0.0 --port 8000
# if __name__ == "__main__":
#     uvicorn.run("backend:app", host="0.0.0.0", port=8000, log_level="info")

