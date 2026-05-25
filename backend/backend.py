
# backend.py
import asyncio
import ipaddress
import json
import logging
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from netbox.api import router as netbox_api_router, process_pending_netbox_retries, sync_local_inventory_to_netbox
from network.api import router as network_router, set_db_manager as set_network_db
from netbox.sync_devices import DeviceSyncer
from netbox.sync_topology import TopologySyncer
from netbox.webhook import router as netbox_webhook_router
from drift.api import router as drift_router
from compliance.api import router as compliance_router
from config_engine.api import router as config_router
from config_engine.git_api import router as template_git_router
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
from device_syslog.api import router as device_syslog_router

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
from common_services.api import router as common_services_router

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
from common_services.service import get_effective_profile
from device_syslog.service import parse_syslog_metadata
from security.models import SecurityEvent
from security.threat.syslog_receiver import SyslogReceiver

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
REALTIME_PULSE_SECONDS = max(5, int(os.getenv("REALTIME_PULSE_SECONDS", "20")))

# Seeds & credentials from environment (no hardcoded secrets)
CRAWL_SEEDS: List[str] = [
    s.strip() for s in os.getenv("CRAWL_SEEDS", "192.168.1.123").split(",") if s.strip()
]
CRAWL_USER     = os.getenv("CRAWL_USER", "")
CRAWL_PASSWORD = os.getenv("CRAWL_PASSWORD", "")
CRAWL_KEYFILE  = os.getenv("CRAWL_KEYFILE") or None
CRAWL_DRIVER   = os.getenv("CRAWL_DRIVER", "cisco_ios")
CRAWL_JUMP     = None  # set via env JSON if needed

CRAWL_AUTO_SUBNET_DISCOVERY = os.getenv("CRAWL_AUTO_SUBNET_DISCOVERY", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
CRAWL_DISCOVERY_SUBNETS: List[str] = [
    s.strip() for s in os.getenv("CRAWL_DISCOVERY_SUBNETS", "").split(",") if s.strip()
]
CRAWL_DISCOVERY_PORT = int(os.getenv("CRAWL_DISCOVERY_PORT", "22"))
CRAWL_DISCOVERY_TIMEOUT_MS = int(os.getenv("CRAWL_DISCOVERY_TIMEOUT_MS", "250"))
CRAWL_DISCOVERY_WORKERS = int(os.getenv("CRAWL_DISCOVERY_WORKERS", "64"))
CRAWL_DISCOVERY_MAX_HOSTS = int(os.getenv("CRAWL_DISCOVERY_MAX_HOSTS", "1024"))

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
_crawl_run_lock = asyncio.Lock()
_last_crawl_finished_at_monotonic: float = 0.0

# Shared instances promoted to module scope so run_crawl_and_broadcast
# can feed every fresh crawl result into the agent's RAG cache.
_db: Optional[DatabaseManager] = None
_agent: Optional[NetworkAgent] = None
_vault = None  # CredentialVault — set in lifespan
_state_embedder = None  # NetworkStateEmbedder — Phase 9
_syslog_receiver: Optional[SyslogReceiver] = None
_syslog_loop: Optional[asyncio.AbstractEventLoop] = None

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

    # Build effective seeds from static env + inventory management IPs so newly
    # registered devices are pollable even before LLDP/CDP adjacency is learned.
    effective_seeds: List[str] = []
    seen_seed_ips: Set[str] = set()
    for ip in CRAWL_SEEDS:
        cleaned = str(ip or "").strip()
        if cleaned and cleaned not in seen_seed_ips:
            seen_seed_ips.add(cleaned)
            effective_seeds.append(cleaned)

    db_seed_count = 0
    db_cred_overrides: Dict[str, Tuple[str, str]] = {}
    if _db is not None:
        try:
            for dev in _db.get_all_devices():
                mgmt_ip = str(getattr(dev, "management_ip", "") or "").strip()
                if not mgmt_ip:
                    continue
                if mgmt_ip not in seen_seed_ips:
                    seen_seed_ips.add(mgmt_ip)
                    effective_seeds.append(mgmt_ip)
                    db_seed_count += 1

                ssh_user = str(getattr(dev, "ssh_user", "") or "").strip()
                ssh_password = getattr(dev, "ssh_password", None)
                if ssh_user and ssh_password:
                    db_cred_overrides[mgmt_ip] = (ssh_user, str(ssh_password))
        except Exception as exc:
            logger.warning("Could not augment crawl seeds from inventory: %s", exc)

    # Allow crawl to proceed from per-device credentials even when no global
    # crawler credentials are configured.
    if not user and db_cred_overrides:
        sample_ip, (sample_user, sample_pass) = next(iter(db_cred_overrides.items()))
        user, password = sample_user, sample_pass
        logger.info(
            "No global crawl credential configured; using inventory credential as default "
            "(seed=%s, user=%s) with per-device overrides",
            sample_ip,
            sample_user,
        )

    if not user:
        logger.error(
            "No crawl credentials configured — set CRAWL_USER/CRAWL_PASSWORD, store a vault default, "
            "or save SSH credentials on at least one inventory device"
        )
        return {"timestamp": datetime.now().isoformat(), "seeds": CRAWL_SEEDS, "nodes": {}}

    if db_seed_count:
        logger.info(
            "Augmented crawler seeds with %d inventory management IP(s); total seeds=%d",
            db_seed_count,
            len(effective_seeds),
        )

    # Optional subnet auto-discovery: quickly probe for new SSH-reachable IPs
    # and include them as additional crawl seeds.
    if CRAWL_AUTO_SUBNET_DISCOVERY:
        discovery_targets: Set[str] = set()
        discovery_networks: List[ipaddress.IPv4Network] = []

        for raw in CRAWL_DISCOVERY_SUBNETS:
            try:
                net = ipaddress.ip_network(raw, strict=False)
            except ValueError:
                logger.warning("Ignoring invalid CRAWL_DISCOVERY_SUBNETS entry: %s", raw)
                continue
            if isinstance(net, ipaddress.IPv4Network):
                discovery_networks.append(net)

        if not discovery_networks:
            # Default behavior: discover inside /24 networks derived from current seeds.
            derived: Set[str] = set()
            for ip in list(effective_seeds):
                try:
                    net = ipaddress.ip_network(f"{ip}/24", strict=False)
                except ValueError:
                    continue
                if isinstance(net, ipaddress.IPv4Network):
                    key = str(net)
                    if key in derived:
                        continue
                    derived.add(key)
                    discovery_networks.append(net)

        for net in discovery_networks:
            for host in net.hosts():
                host_ip = str(host)
                if host_ip in seen_seed_ips:
                    continue
                discovery_targets.add(host_ip)
                if len(discovery_targets) >= max(1, CRAWL_DISCOVERY_MAX_HOSTS):
                    break
            if len(discovery_targets) >= max(1, CRAWL_DISCOVERY_MAX_HOSTS):
                break

        if discovery_targets:
            timeout_seconds = max(10, CRAWL_DISCOVERY_TIMEOUT_MS) / 1000.0

            def _is_ssh_open(ip_addr: str) -> bool:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout_seconds)
                try:
                    return sock.connect_ex((ip_addr, CRAWL_DISCOVERY_PORT)) == 0
                except Exception:
                    return False
                finally:
                    sock.close()

            discovered_ips: List[str] = []
            workers = max(4, CRAWL_DISCOVERY_WORKERS)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_is_ssh_open, ip): ip for ip in discovery_targets}
                for fut in as_completed(futures):
                    ip = futures[fut]
                    try:
                        if fut.result():
                            discovered_ips.append(ip)
                    except Exception:
                        continue

            if discovered_ips:
                for ip in sorted(set(discovered_ips)):
                    if ip not in seen_seed_ips:
                        seen_seed_ips.add(ip)
                        effective_seeds.append(ip)
                logger.info(
                    "Subnet discovery found %d SSH-reachable host(s); crawl seeds now=%d",
                    len(discovered_ips),
                    len(effective_seeds),
                )
            else:
                logger.debug("Subnet discovery scanned %d hosts and found no new SSH targets", len(discovery_targets))

    # Prefer DB/NetBox-synced per-device credentials; env overrides are fallback.
    effective_cred_overrides = dict(_CRAWL_CRED_OVERRIDES)
    effective_cred_overrides.update(db_cred_overrides)

    return _bfs_crawl(
        seeds=effective_seeds,
        username=user,
        password=password,
        key_file=CRAWL_KEYFILE,
        driver_type=CRAWL_DRIVER,
        jump_host=CRAWL_JUMP,
        workers=CRAWL_WORKERS,
        cred_overrides=effective_cred_overrides or None,
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


def _save_security_event(db: DatabaseManager, event: SecurityEvent) -> int:
    with db._connect() as conn:
        cursor = conn.execute(
            """INSERT INTO security_events
               (event_type, severity, source_ip, dest_ip, device_id, description, raw_data, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_type,
                event.severity,
                event.source_ip,
                event.dest_ip,
                event.device_id,
                event.description,
                json.dumps(event.raw_data) if event.raw_data else None,
                event.status,
            ),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def _ingest_syslog_event(event: SecurityEvent):
    db = _db or DatabaseManager("db/netauto.db")
    try:
        event_id = _save_security_event(db, event)
        await _telemetry_broadcast(
            {
                "event": "security_event",
                "data": {
                    "id": event_id,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "source_ip": event.source_ip,
                    "dest_ip": event.dest_ip,
                    "device_id": event.device_id,
                    "description": event.description,
                    "status": event.status,
                    "created_at": event.created_at,
                },
            }
        )
        logger.info("Syslog event ingested: %s from %s", event.event_type, event.source_ip or "unknown")
    except Exception as exc:
        logger.warning("Failed to ingest syslog event: %s", exc)


def _handle_syslog_event(event: SecurityEvent):
    loop = _syslog_loop
    if not loop or loop.is_closed():
        logger.warning("Syslog event dropped because the application loop is unavailable")
        return
    asyncio.run_coroutine_threadsafe(_ingest_syslog_event(event), loop)


async def _ingest_raw_syslog(source_ip: str, raw_message: str):
    db = _db or DatabaseManager("db/netauto.db")
    try:
        parsed = parse_syslog_metadata(raw_message)
        resolved_source_ip = source_ip
        resolved_source_host = parsed.get("source_host")

        # When syslog is relayed (e.g., via a syslog server), transport source IP
        # can be the relay itself. Prefer embedded identifier and resolve it
        # against inventory to recover the true originating device.
        if resolved_source_host:
            ident = str(resolved_source_host).strip()
            with db._connect() as conn:
                row = conn.execute(
                    "SELECT id, hostname, management_ip FROM devices "
                    "WHERE lower(hostname) = lower(?) OR management_ip = ? "
                    "ORDER BY id ASC LIMIT 1",
                    (ident, ident),
                ).fetchone()
            if row:
                resolved_source_host = str(row["hostname"] or ident)
                resolved_source_ip = str(row["management_ip"] or resolved_source_ip)

        row_id = db.insert_device_syslog_event(
            source_ip=resolved_source_ip,
            source_host=resolved_source_host,
            raw_message=raw_message,
            severity=parsed.get("severity"),
            facility=parsed.get("facility"),
            mnemonic=parsed.get("mnemonic"),
            message=parsed.get("message"),
        )
        await _telemetry_broadcast(
            {
                "event": "device_syslog_event",
                "data": {
                    "id": row_id,
                    "source_ip": resolved_source_ip,
                    "source_host": resolved_source_host,
                    "severity": parsed.get("severity"),
                    "facility": parsed.get("facility"),
                    "mnemonic": parsed.get("mnemonic"),
                    "message": parsed.get("message"),
                },
            }
        )
    except Exception as exc:
        logger.warning("Failed to ingest raw syslog line: %s", exc)


def _handle_raw_syslog(source_ip: str, raw_message: str):
    loop = _syslog_loop
    if not loop or loop.is_closed():
        logger.warning("Raw syslog line dropped because the application loop is unavailable")
        return
    asyncio.run_coroutine_threadsafe(_ingest_raw_syslog(source_ip, raw_message), loop)

def save_topology_to_file(topo: dict, filepath: str, sort_keys: bool = False):
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(topo, f, indent=2, sort_keys=sort_keys)
        logger.info(f"Saved topology to {filepath}")
    except Exception as e:
        logger.error(f"Failed to save topology to {filepath}: {e}")

async def run_crawl_and_broadcast(trigger: str = "periodic"):
    global _current_topology_dict, _current_topology_json, _previous_topology_dict, _last_crawl_finished_at_monotonic
    if _crawl_run_lock.locked():
        logger.info("Skipping crawl (trigger=%s): another crawl is already running", trigger)
        return

    async with _crawl_run_lock:
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

        _last_crawl_finished_at_monotonic = time.monotonic()
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
    global _syslog_receiver, _syslog_loop
    _syslog_loop = asyncio.get_running_loop()
    try:
        effective_services = get_effective_profile("db/netauto.db")
        profile_port = int(effective_services.get("syslog_port") or 6514)
    except Exception:
        profile_port = 6514
    env_receiver_port = str(os.getenv("BACKEND_SYSLOG_RECEIVER_PORT", "")).strip()
    if env_receiver_port.isdigit():
        syslog_port = int(env_receiver_port)
        port_source = "BACKEND_SYSLOG_RECEIVER_PORT"
    else:
        syslog_port = profile_port
        port_source = "common_services.syslog_port"
    try:
        _syslog_receiver = SyslogReceiver(
            port=syslog_port,
            callback=_handle_syslog_event,
            raw_callback=_handle_raw_syslog,
        )
        _syslog_receiver.start()
        logger.info("Syslog receiver initialized (udp/%d, source=%s)", syslog_port, port_source)
    except Exception as exc:
        logger.warning("Syslog receiver failed to start: %s", exc)
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
    start_scheduler(
        run_crawl_and_broadcast,
        orchestrator=_orchestrator,
        broadcast_fn=_telemetry_broadcast,
        inventory_sync_fn=lambda: DeviceSyncer(db).sync_all_devices(),
        local_to_netbox_sync_fn=sync_local_inventory_to_netbox,
    )
    logger.info("Background scheduler started")

    # 5b. Real-time pulse crawl: while dashboard WS clients are connected,
    # trigger periodic non-overlapping crawls for near real-time topology.
    async def _realtime_crawl_pulse():
        while True:
            await asyncio.sleep(REALTIME_PULSE_SECONDS)
            try:
                async with mgr.lock:
                    has_realtime_clients = bool(mgr.active)
                if not has_realtime_clients:
                    continue
                await run_crawl_and_broadcast(trigger="realtime_pulse")
            except Exception as exc:
                logger.warning("Realtime crawl pulse failed: %s", exc)

    realtime_pulse_task = asyncio.create_task(_realtime_crawl_pulse())
    logger.info("Realtime crawl pulse started (interval=%ss)", REALTIME_PULSE_SECONDS)

    # NetBox retry worker: periodically retries failed remote NetBox syncs
    async def _netbox_retry_worker():
        while True:
            await asyncio.sleep(30)
            try:
                stats = process_pending_netbox_retries(limit=20)
                if (stats.get("processed") or 0) > 0:
                    logger.info(
                        "NetBox retry worker: processed=%s succeeded=%s failed=%s",
                        stats.get("processed"),
                        stats.get("succeeded"),
                        stats.get("failed"),
                    )
            except Exception as exc:
                logger.warning("NetBox retry worker failed: %s", exc)

    netbox_retry_task = asyncio.create_task(_netbox_retry_worker())
    logger.info("NetBox retry worker started (interval=30s)")

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
    realtime_pulse_task.cancel()
    netbox_retry_task.cancel()
    if _syslog_receiver:
        _syslog_receiver.stop()
    stop_scheduler()

app = FastAPI(title="CogniShield Backend", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS — belt-and-suspenders approach
# ---------------------------------------------------------------------------
# 1) Standard CORSMiddleware for normal responses
# 2) A catch-all middleware that guarantees CORS headers even on unhandled
#    500 errors (which can bypass CORSMiddleware in some Starlette versions).

# Allow configuring CORS origins via environment for flexible dev/network setups.
# Set CORS_ALLOWED_ORIGINS to a comma-separated list of origins, or set CORS_ALLOW_ALL=true
# to allow all origins (not recommended for production when credentials are used).
_cors_allow_all = os.getenv("CORS_ALLOW_ALL", "false").strip().lower() in {"1", "true", "yes"}
_cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
if _cors_allow_all:
    _ALLOWED_ORIGINS = ["*"]
elif _cors_env:
    _ALLOWED_ORIGINS = [s.strip() for s in _cors_env.split(",") if s.strip()]
else:
    _ALLOWED_ORIGINS = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        ""
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=not _cors_allow_all,
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
        # Guarantee CORS header on every response (including errors).
        # If wildcard origins are enabled, echo the incoming Origin.
        if _cors_allow_all:
            allowed_origin = origin or "*"
        else:
            allowed_origin = origin if origin in _ALLOWED_ORIGINS else None

        if allowed_origin and "access-control-allow-origin" not in response.headers:
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
app.include_router(template_git_router, prefix="/api/v1", tags=["Template Studio Git"])
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
app.include_router(common_services_router, prefix="/api/v1", tags=["Common Services"])
app.include_router(device_syslog_router, prefix="/api/v1", tags=["Syslog"])

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

        # Kick an immediate refresh for near-real-time visibility when clients
        # open the dashboard. This runs async and is protected by crawl lock.
        asyncio.create_task(run_crawl_and_broadcast(trigger="ws_client_connected"))
        
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
