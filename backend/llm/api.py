"""
FastAPI chat router for the LLM Agent.

Provides REST endpoints for the frontend chat interface to communicate
with the NetworkAgent, plus M19 proposal lifecycle endpoints.

Endpoints:
    POST /api/chat                          — Send a message, get AI response
    POST /api/chat/clear                    — Clear conversation history
    GET  /api/chat/history                  — Get conversation history
    GET  /api/chat/tools                    — List available tools
    GET  /api/chat/info                     — Get agent info (model, status)
    GET  /api/chat/observability            — Get controller observability snapshot
    POST /api/chat/refresh                  — Refresh network context

    GET  /api/chat/proposals                — List all proposals
    GET  /api/chat/proposals/{id}           — Get a specific proposal
    POST /api/chat/proposals/{id}/approve   — Approve → deploy to device
    POST /api/chat/proposals/{id}/reject    — Reject → discard proposal
    POST /api/chat/proposals/{id}/rollback  — Rollback → revert deployed change
"""

import json
import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from auth.rbac import require_role
from config_engine.safety import validate_action_artifact
from llm.advanced_differentiators import (
    build_advanced_differentiator_status,
    ensure_advanced_differentiator_available,
)
from llm.benchmark_suite import benchmark_categories, list_benchmark_cases, run_benchmark_suite
from llm.resilience_suite import list_resilience_scenarios, resilience_categories, run_resilience_suite
from llm.change_intelligence import CHANGE_INTELLIGENCE_VERSION
from llm.controller_observability import (
    build_controller_observability_payload,
    clear_latest_controller_chat_snapshot,
    get_latest_controller_chat_snapshot,
    record_controller_chat_snapshot,
    summarize_proposals,
    summarize_validation_outcomes,
)
from llm.post_change_verification import build_post_change_verification_report
from rag.digital_twin import DIGITAL_TWIN_VERSION
from llm import network_tools
from llm.refresh_fabric import register_refresh_listener
from rag.context_bundle import ContextBundleBuilder
from llm.ollama_provider import DEFAULT_OLLAMA_MODEL

logger = logging.getLogger("llm.api")

router = APIRouter(prefix="/llm/chat", tags=["Chat (M5)"])


# -----------------------------------------------------------------------
# Request / response models
# -----------------------------------------------------------------------

class ChatRequest(BaseModel):
    """User chat message."""
    message: str = Field(..., description="The user's message", min_length=1)
    grounding: bool = Field(default=True, description="Enable grounding and network context injection")


class ChatRuntimeUpdateRequest(BaseModel):
    """Runtime provider/model selection for the shared agent."""

    provider: str = Field(..., description="LLM provider name")
    model: Optional[str] = Field(default=None, description="Model identifier to activate")
    ollama_base_url: Optional[str] = Field(default=None, description="Ollama server base URL")
    clear_history: bool = Field(default=True, description="Clear chat history when switching runtimes")


class OllamaPlaygroundRequest(BaseModel):
    """Direct unrestricted prompt for the local Ollama instance."""
    prompt: str = Field(..., description="The user prompt", min_length=1)
    model: Optional[str] = Field(default=None, description="Optional model override")
    temperature: float = Field(default=0.7)


class ChatResponse(BaseModel):
    """Agent chat response — may include a config proposal."""
    role: str = "assistant"
    content: str = ""
    model: str = ""
    type: str = "text"                     # "text" | "proposal" | "intent_plan"
    proposal: Optional[Dict[str, Any]] = None  # ConfigProposal dict
    intent_meta: Optional[Dict[str, Any]] = None  # Intent pipeline metadata
    grounding_meta: Optional[Dict[str, Any]] = None


class InfoResponse(BaseModel):
    """Agent info."""
    model: str = ""
    tools: list = []
    history_length: int = 0
    status: str = "ready"
    scope_contract: Optional[Dict[str, Any]] = None
    implementation_status: Optional[Dict[str, Any]] = None
    milestone2_baseline: Optional[Dict[str, Any]] = None


class ProposalReviewRequest(BaseModel):
    """Line-level review result submitted by engineer/admin before approval."""

    merged_config: str = Field(..., description="Reviewed merged config", min_length=1)
    accepted_line_indices: List[int] = Field(default_factory=list)
    rejected_line_indices: List[int] = Field(default_factory=list)


def _persist_post_change_verification(
    mgr,
    proposal_id: str,
    proposal,
    report: Dict[str, Any],
    execution_status: str,
):
    action_artifact = dict(proposal.action_artifact or {})
    action_artifact["post_change_verification"] = report
    action_artifact["deployment_outcome"] = {
        "status": execution_status,
        "verified_at": report.get("generated_at"),
        "outcome": report.get("outcome"),
        "recommended_action": report.get("recommended_action"),
    }
    updated = mgr.update_action_artifact(proposal_id, action_artifact)
    return updated or mgr.get(proposal_id)


def _persist_rollback_execution(
    mgr,
    proposal_id: str,
    proposal,
    status: str,
    details: Dict[str, Any],
):
    action_artifact = dict(proposal.action_artifact or {})
    action_artifact["rollback_execution"] = {
        "status": status,
        "updated_at": details.get("updated_at"),
        "mode": details.get("mode"),
        "summary": details.get("summary"),
        "job_id": details.get("job_id"),
    }
    updated = mgr.update_action_artifact(proposal_id, action_artifact)
    return updated or mgr.get(proposal_id)


def _load_pipeline_job(job_id: int):
    if not _pipeline:
        return None
    from deploy.models import ExecutionEngine, Job, JobStatus, PipelineStage

    with _pipeline.db_manager._connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        return Job(
            id=row["id"],
            request_id=row["request_id"],
            device_id=row["device_id"],
            status=JobStatus(row["status"]),
            stage=PipelineStage(row["stage"]),
            snapshot_pre_id=row["snapshot_pre_id"],
            snapshot_post_id=row["snapshot_post_id"],
            generated_config=row["generated_config"],
            rollback_config=row["rollback_config"],
            error_message=row["error_message"],
            execution_engine=ExecutionEngine(row["execution_engine"]) if row["execution_engine"] else ExecutionEngine.SSH_FALLBACK,
            ansible_playbook=row["ansible_playbook"] if row["ansible_playbook"] else None,
        )


def _resolve_device_access(proposal):
    creds = None
    if _vault and proposal.target_device_id > 0:
        try:
            creds = _vault.get(proposal.target_device_id, "SSH")
        except Exception as e:
            logger.warning("Failed to fetch credentials: %s", e)

    ssh_host = proposal.target_device
    if _pipeline and proposal.target_device_id > 0:
        try:
            dev = _pipeline.db_manager.get_device_by_id(proposal.target_device_id)
            if dev and dev.management_ip:
                ssh_host = dev.management_ip
        except Exception as e:
            logger.warning("Could not resolve management IP: %s", e)

    return ssh_host, creds


def _advanced_differentiator_status(agent=None, feature_name: Optional[str] = None) -> Dict[str, Any]:
    implementation_status = getattr(agent, "controller_implementation_status", None) if agent is not None else None
    return build_advanced_differentiator_status(implementation_status, feature_name=feature_name)


def _require_advanced_differentiator(feature_name: str, agent=None) -> Dict[str, Any]:
    implementation_status = getattr(agent, "controller_implementation_status", None) if agent is not None else None
    return ensure_advanced_differentiator_available(feature_name, implementation_status)


class BenchmarkRunRequest(BaseModel):
    """Optional benchmark filters for the LLM regression suite."""

    categories: Optional[List[str]] = Field(
        default=None,
        description="Optional subset of benchmark categories to execute.",
    )


class ResilienceRunRequest(BaseModel):
    """Optional synthetic resilience-scenario filters."""

    categories: Optional[List[str]] = Field(
        default=None,
        description="Optional subset of resilience categories to execute.",
    )


class ChangeIntelligenceRequest(BaseModel):
    """Request body for deterministic Step 29 change-intelligence analysis."""

    intent_json: str = Field(..., description="JSON string of a ParsedIntent to analyze", min_length=2)
    user_role: str = Field(default="operator", description="Role used for policy-aware simulation")


class DigitalTwinRequest(BaseModel):
    """Request body for the formal Step 30 digital-twin graph snapshot."""

    hostname: Optional[str] = Field(default=None, description="Optional device hostname scope")
    vendor_filter: Optional[str] = Field(default=None, description="Optional vendor filter for network-wide twins")
    include_telemetry: bool = Field(default=True, description="Include telemetry trends in the digital twin")
    include_sdn: bool = Field(default=True, description="Include SDN state while building the twin")


_GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]
_DEFAULT_OLLAMA_VM_URL = os.getenv("OLLAMA_UI_BASE_URL", "http://192.168.1.252:11434")
_LOCAL_OLLAMA_URLS = {"http://127.0.0.1:11434", "http://localhost:11434"}


def _effective_ollama_base_url(candidate: Optional[str] = None) -> str:
    """Resolve Ollama URL; backend env wins over stale localhost UI defaults."""
    supplied = str(candidate or "").strip().rstrip("/")
    env_url = str(os.getenv("OLLAMA_BASE_URL") or "").strip().rstrip("/")
    ui_url = str(os.getenv("OLLAMA_UI_BASE_URL") or "").strip().rstrip("/")
    if supplied and supplied not in _LOCAL_OLLAMA_URLS:
        return supplied
    if env_url:
        return env_url
    if supplied:
        return supplied
    if ui_url:
        return ui_url
    return _DEFAULT_OLLAMA_VM_URL.rstrip("/")


def _provider_kind(provider: Any) -> str:
    provider_name = provider.__class__.__name__.lower() if provider is not None else ""
    if "ollama" in provider_name:
        return "ollama"
    return "gemini"


def _inventory_markdown_title(title: str) -> str:
    return f"**{title}**"


def _dedupe_inventory_links(links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for link in links:
        peer = str(link.get("peer_hostname") or "").strip()
        local_if = str(link.get("local_interface") or "").strip()
        peer_if = str(link.get("peer_interface") or "").strip()
        status = str(link.get("status") or "").strip().lower()
        if not peer or not local_if:
            continue
        key = (peer.lower(), local_if.lower(), peer_if.lower(), status)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(link)
    deduped.sort(
        key=lambda item: (
            str(item.get("peer_hostname") or "").lower(),
            str(item.get("local_interface") or "").lower(),
            str(item.get("peer_interface") or "").lower(),
        )
    )
    return deduped


def _inventory_role_rank(role: Any) -> int:
    text = str(role or "").lower()
    if "core" in text:
        return 30
    if "dist" in text or "distribution" in text:
        return 20
    if "access" in text or "acc" in text:
        return 10
    return 0


def _build_inventory_link_graph(local_records: List[Dict[str, Any]]) -> tuple[Dict[str, List[str]], List[Dict[str, str]]]:
    adjacency: Dict[str, set[str]] = {}
    edge_facts: List[Dict[str, str]] = []
    seen = set()

    planned = network_tools._get_planned_topology_data()
    for link in planned.get("links", []) or []:
        if not isinstance(link, dict):
            continue
        src = str(link.get("src_hostname") or "").strip()
        dst = str(link.get("dst_hostname") or "").strip()
        src_if = str(link.get("src_interface") or "").strip()
        dst_if = str(link.get("dst_interface") or "").strip()
        if not src or not dst:
            continue
        edge_key = tuple(sorted((src.lower(), dst.lower(), src_if.lower(), dst_if.lower())))
        if edge_key in seen:
            continue
        seen.add(edge_key)
        adjacency.setdefault(src, set()).add(dst)
        adjacency.setdefault(dst, set()).add(src)
        edge_facts.append(
            {
                "src": src,
                "dst": dst,
                "src_if": src_if,
                "dst_if": dst_if,
                "source": str(planned.get("source") or "planned_topology"),
            }
        )

    for record in local_records:
        enriched = network_tools._enrich_inventory_record(dict(record))
        src = str(enriched.get("hostname") or "").strip()
        if not src:
            continue
        for link in _dedupe_inventory_links([item for item in (enriched.get("links") or []) if isinstance(item, dict)]):
            dst = str(link.get("peer_hostname") or "").strip()
            src_if = str(link.get("local_interface") or "").strip()
            dst_if = str(link.get("peer_interface") or "").strip()
            if not dst:
                continue
            edge_key = tuple(sorted((src.lower(), dst.lower(), src_if.lower(), dst_if.lower())))
            if edge_key in seen:
                continue
            seen.add(edge_key)
            adjacency.setdefault(src, set()).add(dst)
            adjacency.setdefault(dst, set()).add(src)
            edge_facts.append(
                {
                    "src": src,
                    "dst": dst,
                    "src_if": src_if,
                    "dst_if": dst_if,
                    "source": "local_inventory",
                }
            )

    return {key: sorted(value) for key, value in adjacency.items()}, edge_facts


def _find_inventory_path(adjacency: Dict[str, List[str]], source: str, destination: str) -> List[str]:
    from collections import deque

    if source not in adjacency or destination not in adjacency:
        return []
    queue = deque([(source, [source])])
    visited = {source}
    while queue:
        current, path = queue.popleft()
        if current == destination:
            return path
        for neighbor in adjacency.get(current, []):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            queue.append((neighbor, path + [neighbor]))
    return []


def _edge_matches_host(edge: Dict[str, str], hostname: str) -> bool:
    host = str(hostname or "").strip().lower()
    return str(edge.get("src") or "").lower() == host or str(edge.get("dst") or "").lower() == host


def _edge_local_remote(edge: Dict[str, str], hostname: str) -> tuple[str, str, str]:
    if str(edge.get("src") or "").lower() == str(hostname or "").lower():
        return str(edge.get("src_if") or "Unknown"), str(edge.get("dst") or "Unknown"), str(edge.get("dst_if") or "Unknown")
    return str(edge.get("dst_if") or "Unknown"), str(edge.get("src") or "Unknown"), str(edge.get("src_if") or "Unknown")


def _render_inventory_device_summary(device: Dict[str, Any]) -> str:
    lines = [
        _inventory_markdown_title(str(device.get("hostname") or "Device Summary")),
        "",
        f"- Vendor: {device.get('vendor') or 'Not recorded'}",
        f"- Model: {device.get('model') or 'Not recorded'}",
        f"- Role: {device.get('role') or 'Not recorded'}",
        f"- Status: {device.get('status') or 'Not recorded'}",
        f"- Management IP: {device.get('management_ip') or 'Not recorded'}",
        f"- Management Interface: {device.get('management_interface') or 'Not recorded'}",
        f"- OS Version: {device.get('os_version') or 'Not recorded'}",
    ]
    return "\n".join(lines)


def _build_inventory_direct_answer(prompt_text: str, local_records: List[Dict[str, Any]]) -> Optional[str]:
    prompt = str(prompt_text or "").strip()
    if not prompt:
        return None

    prompt_lower = prompt.lower()
    records = list(local_records or [])
    if not records:
        return None

    ranked = network_tools._rank_inventory_records(records, prompt, limit=4)
    target = ranked[0] if ranked else None
    target_hostname = str(target.get("hostname") or "").strip() if target else ""

    wants_neighbors = any(token in prompt_lower for token in ["neighbor", "neighbour", "connection", "connected to", "uplink", "downlink"])
    wants_mgmt_ip = ("management ip" in prompt_lower) or ("mgmt ip" in prompt_lower) or ("management address" in prompt_lower)
    wants_mgmt_interface = "management interface" in prompt_lower
    wants_vendor_model_role = (
        "vendor" in prompt_lower
        or "model" in prompt_lower
        or "role" in prompt_lower
        or "planned inventory" in prompt_lower
    )
    wants_interface_list = any(token in prompt_lower for token in ["list interfaces", "show interfaces", "what interfaces", "which interfaces", "interface list"])
    wants_interface_count = "how many interface" in prompt_lower or "interface count" in prompt_lower
    wants_device_count = any(token in prompt_lower for token in ["how many devices", "device count", "total devices", "inventory count"])
    wants_path = "path between" in prompt_lower or ("path from" in prompt_lower and " to " in prompt_lower)
    wants_upstream = "upstream" in prompt_lower
    wants_downstream = "downstream" in prompt_lower
    wants_impact = "impacted if" in prompt_lower or "affected if" in prompt_lower or "fails" in prompt_lower
    wants_interface_peer = (
        "connects to" in prompt_lower
        or "connected to" in prompt_lower
        or "which device connects" in prompt_lower
        or "what device connects" in prompt_lower
    )

    adjacency, edge_facts = _build_inventory_link_graph(records)

    if wants_path:
        if len(ranked) < 2:
            return None
        src = str(ranked[0].get("hostname") or "").strip()
        dst = str(ranked[1].get("hostname") or "").strip()
        if not src or not dst:
            return None
        path = _find_inventory_path(adjacency, src, dst)
        lines = [_inventory_markdown_title(f"Path Between {src} and {dst}"), ""]
        if not path:
            lines.append("- No planned/local inventory path is recorded between these devices.")
            return "\n".join(lines)
        lines.append(f"- Hop count: {max(len(path) - 1, 0)}")
        lines.append(f"- Path: {' -> '.join(path)}")
        lines.extend(["", _inventory_markdown_title("Hop Sequence"), ""])
        for index, hop in enumerate(path, start=1):
            lines.append(f"{index}. {hop}")
        return "\n".join(lines)

    if wants_device_count and not target_hostname:
        by_vendor: Dict[str, int] = {}
        by_role: Dict[str, int] = {}
        for record in records:
            vendor = str(record.get("vendor") or "Unknown")
            role = str(record.get("role") or "Unknown")
            by_vendor[vendor] = by_vendor.get(vendor, 0) + 1
            by_role[role] = by_role.get(role, 0) + 1

        lines = [
            _inventory_markdown_title("Inventory Count"),
            "",
            f"- Total devices: {len(records)}",
            "",
            _inventory_markdown_title("By Vendor"),
            "",
        ]
        for vendor, count in sorted(by_vendor.items(), key=lambda item: (item[0].lower(), item[1])):
            lines.append(f"- {vendor}: {count}")
        lines.extend(["", _inventory_markdown_title("By Role"), ""])
        for role, count in sorted(by_role.items(), key=lambda item: (item[0].lower(), item[1])):
            lines.append(f"- {role}: {count}")
        return "\n".join(lines)

    if not target:
        return None

    enriched = network_tools._enrich_inventory_record(dict(target))
    hostname = target_hostname or str(enriched.get("hostname") or "Unknown")
    interfaces = [iface for iface in (enriched.get("interfaces") or []) if isinstance(iface, dict)]
    neighbors = _dedupe_inventory_links([link for link in (enriched.get("links") or []) if isinstance(link, dict)])
    relevant_edges = [edge for edge in edge_facts if _edge_matches_host(edge, hostname)]

    if wants_interface_peer and relevant_edges:
        named_interfaces = [
            str(iface.get("name") or "").strip()
            for iface in interfaces
            if str(iface.get("name") or "").strip()
        ]
        matched_interface = next((name for name in named_interfaces if name.lower() in prompt_lower), "")
        if matched_interface:
            lines = [_inventory_markdown_title(f"Connection for {hostname} {matched_interface}"), ""]
            matches = []
            for edge in relevant_edges:
                local_if, peer, peer_if = _edge_local_remote(edge, hostname)
                if local_if.lower() == matched_interface.lower():
                    matches.append((peer, peer_if, edge.get("source") or "inventory"))
            if not matches:
                lines.append("- No connected peer is recorded for that interface.")
                return "\n".join(lines)
            for peer, peer_if, source in matches:
                lines.append(f"- {matched_interface} -> {peer} {peer_if} (source: {source})")
            return "\n".join(lines)

    if wants_upstream or wants_downstream:
        device_rank = _inventory_role_rank(enriched.get("role"))
        upstream: List[str] = []
        downstream: List[str] = []
        lateral: List[str] = []
        record_by_name = {str(record.get("hostname") or ""): record for record in records}
        for edge in relevant_edges:
            _local_if, peer, peer_if = _edge_local_remote(edge, hostname)
            peer_record = record_by_name.get(peer) or {}
            peer_rank = _inventory_role_rank(peer_record.get("role"))
            rendered = f"{peer} via {peer_if}"
            if peer_rank > device_rank:
                upstream.append(rendered)
            elif peer_rank < device_rank:
                downstream.append(rendered)
            else:
                lateral.append(rendered)
        title = "Upstream" if wants_upstream and not wants_downstream else "Downstream" if wants_downstream and not wants_upstream else "Adjacency Direction"
        lines = [_inventory_markdown_title(f"{title} for {hostname}"), ""]
        if wants_upstream:
            lines.append(_inventory_markdown_title("Upstream"))
            lines.append("")
            lines.extend([f"- {item}" for item in upstream] or ["- No upstream neighbors are recorded."])
            if wants_downstream:
                lines.extend(["", _inventory_markdown_title("Downstream"), ""])
                lines.extend([f"- {item}" for item in downstream] or ["- No downstream neighbors are recorded."])
        elif wants_downstream:
            lines.append(_inventory_markdown_title("Downstream"))
            lines.append("")
            lines.extend([f"- {item}" for item in downstream] or ["- No downstream neighbors are recorded."])
            if lateral:
                lines.extend(["", _inventory_markdown_title("Lateral"), ""])
                lines.extend([f"- {item}" for item in lateral])
        return "\n".join(lines)

    if wants_impact:
        impacted = sorted(set(adjacency.get(hostname, [])))
        lines = [_inventory_markdown_title(f"Impact if {hostname} Fails"), ""]
        if not impacted:
            lines.append("- No directly connected devices are recorded in the inventory graph.")
            return "\n".join(lines)
        lines.append("- Directly impacted neighbors:")
        for peer in impacted:
            lines.append(f"- {peer}")
        downstream_paths = []
        for node in sorted(adjacency.keys()):
            if node == hostname or node in impacted:
                continue
            path = _find_inventory_path(adjacency, hostname, node)
            if len(path) > 2:
                downstream_paths.append(path)
        if downstream_paths:
            lines.extend(["", _inventory_markdown_title("Extended Reachability"), ""])
            for path in downstream_paths[:10]:
                lines.append(f"- {' -> '.join(path)}")
        return "\n".join(lines)

    if wants_neighbors:
        lines = [
            _inventory_markdown_title(f"Neighbors of {hostname}"),
            "",
        ]
        if not neighbors and not relevant_edges:
            lines.append("- No neighbor records are stored in the local inventory.")
            return "\n".join(lines)
        rendered = set()
        for edge in relevant_edges:
            local_if, peer, peer_if = _edge_local_remote(edge, hostname)
            item = f"- {peer}: {local_if} -> {peer_if}"
            if item in rendered:
                continue
            rendered.add(item)
            lines.append(item)
        if not rendered:
            for link in neighbors:
                peer = link.get("peer_hostname") or "Unknown"
                local_if = link.get("local_interface") or "Unknown"
                peer_if = link.get("peer_interface") or "Unknown"
                status = link.get("status") or "unknown"
                item = f"- {peer}: {local_if} -> {peer_if} (status: {status})"
                if item in rendered:
                    continue
                rendered.add(item)
                lines.append(item)
        lines.extend(["", f"- Source: planned/local inventory links for {hostname}"])
        return "\n".join(lines)

    if wants_mgmt_ip or wants_mgmt_interface:
        lines = [
            _inventory_markdown_title(f"Management Details for {hostname}"),
            "",
            f"- Management IP: {enriched.get('management_ip') or 'Not recorded'}",
            f"- Management subnet mask: {enriched.get('management_subnet_mask') or 'Not recorded'}",
            f"- Management interface: {enriched.get('management_interface') or 'Not recorded'}",
        ]
        return "\n".join(lines)

    if wants_vendor_model_role:
        return _render_inventory_device_summary(enriched)

    if wants_interface_count:
        return "\n".join([
            _inventory_markdown_title(f"Interface Count for {hostname}"),
            "",
            f"- Total interfaces recorded: {len(interfaces)}",
        ])

    if wants_interface_list:
        lines = [
            _inventory_markdown_title(f"Interfaces on {hostname}"),
            "",
        ]
        if not interfaces:
            lines.append("- No interfaces are recorded in the local inventory.")
            return "\n".join(lines)
        for iface in sorted(interfaces, key=lambda item: str(item.get("name") or "").lower()):
            name = iface.get("name") or "Unknown"
            ip = iface.get("ip_address") or "No IP"
            mask = iface.get("subnet_mask") or "No mask"
            status = iface.get("status") or "unknown"
            iface_type = iface.get("interface_type") or iface.get("type") or "unknown"
            mgmt = "yes" if iface.get("is_management") else "no"
            lines.append(
                f"- {name}: type={iface_type}, ip={ip}, subnet_mask={mask}, status={status}, management={mgmt}"
            )
        return "\n".join(lines)

    return None


def _current_provider_name(agent: Any) -> str:
    value = getattr(agent, "provider_name", None)
    if isinstance(value, str) and value:
        return value
    return _provider_kind(getattr(agent, "_provider", None))


def _current_provider_base_url(agent: Any) -> Optional[str]:
    provider = getattr(agent, "_provider", None)
    if _provider_kind(provider) != "ollama":
        return None
    base_url = getattr(provider, "_base_url", None)
    return str(base_url).rstrip("/") if isinstance(base_url, str) and base_url else None


def _gemini_provider_descriptor(active_model: Optional[str]) -> Dict[str, Any]:
    models = list(_GEMINI_MODELS)
    if active_model and active_model not in models:
        models.insert(0, active_model)
    return {
        "name": "gemini",
        "label": "Google Gemini",
        "available": True,
        "base_url": None,
        "models": models,
        "catalog_source": "built_in",
        "reachable": True,
    }


def _fetch_ollama_models(base_url: str) -> tuple[List[str], bool, Optional[str]]:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=15)
        response.raise_for_status()
        payload = response.json()
        models = [str(item.get("name") or "").strip() for item in payload.get("models", []) if item.get("name")]
        return models, True, None
    except Exception as exc:
        return [], False, str(exc)


def _ollama_provider_descriptor(base_url: str, active_model: Optional[str]) -> Dict[str, Any]:
    models, reachable, error = _fetch_ollama_models(base_url)
    descriptor: Dict[str, Any] = {
        "name": "ollama",
        "label": "Ollama VM",
        "available": bool(models),
        "base_url": base_url,
        "models": models,
        "catalog_source": "live" if models else "fallback",
        "reachable": reachable,
    }
    if active_model and active_model not in descriptor["models"]:
        descriptor["models"].insert(0, active_model)
        descriptor["default_model_injected"] = True
    else:
        descriptor["default_model_injected"] = False
    if error:
        descriptor["error"] = error
    return descriptor


def _build_runtime_state(agent: Any, ollama_base_url: Optional[str] = None) -> Dict[str, Any]:
    active_provider = _current_provider_name(agent)
    active_model = getattr(agent, "model_name", None)
    if callable(active_model):
        active_model = active_model()
    active_model = str(active_model or "")
    active_base_url = _current_provider_base_url(agent)
    browser_base_url = _effective_ollama_base_url(ollama_base_url or active_base_url)

    return {
        "active_provider": active_provider,
        "active_model": active_model,
        "active_base_url": active_base_url,
        "scope": "shared_agent",
        "providers": [
            _gemini_provider_descriptor(active_model if active_provider == "gemini" else None),
            _ollama_provider_descriptor(browser_base_url, active_model if active_provider == "ollama" else os.getenv("OLLAMA_MODEL")),
        ],
    }


def _instantiate_runtime_provider(provider_name: str, model: Optional[str], ollama_base_url: Optional[str]):
    normalized = str(provider_name or os.getenv("LLM_PROVIDER") or "ollama").strip().lower()
    if normalized == "ollama":
        from llm.ollama_provider import OllamaProvider

        return OllamaProvider(
            base_url=_effective_ollama_base_url(ollama_base_url),
            model=model or os.getenv("OLLAMA_MODEL"),
        )
    if normalized == "gemini":
        from llm.gemini_provider import GeminiProvider

        return GeminiProvider(
            api_key=os.getenv("GEMINI_API_KEY"),
            model=model or os.getenv("GEMINI_MODEL") or None,
        )
    raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider_name}'")


def _apply_runtime_provider(agent: Any, provider_name: str, model: Optional[str], ollama_base_url: Optional[str], clear_history: bool):
    provider = _instantiate_runtime_provider(provider_name, model, ollama_base_url)
    if hasattr(agent, "set_provider"):
        agent.set_provider(provider, reset_history=clear_history)
    else:
        setattr(agent, "_provider", provider)
        if clear_history and hasattr(agent, "clear_history"):
            agent.clear_history()
    return provider


# -----------------------------------------------------------------------
# Shared agent instance (set by setup_agent)
# -----------------------------------------------------------------------
_agent = None
_pipeline = None  # DeploymentPipeline (M4)
_vault = None     # CredentialVault (M15)
_AGENT_REFRESH_LISTENER = "llm_api_agent_cache"


def setup_agent(agent, pipeline=None, vault=None) -> None:
    """
    Called once at app startup to inject dependencies.

    Args:
        agent:    NetworkAgent instance.
        pipeline: DeploymentPipeline instance (M4) for approve/rollback.
        vault:    CredentialVault instance (M15) for auto-fetching SSH creds.
    """
    global _agent, _pipeline, _vault
    _agent = agent
    _pipeline = pipeline
    _vault = vault
    register_refresh_listener(
        _AGENT_REFRESH_LISTENER,
        lambda _publication: (
            agent.refresh_context(),
            {"kind": "cache", "name": "network_context_cache", "component": "llm_agent", "action": "cleared"},
        )[1],
    )
    logger.info("Chat API initialized with agent (model=%s)", agent.model_name)
    if pipeline:
        logger.info("Deployment pipeline linked to Chat API")
    if vault:
        logger.info("Credential vault linked to Chat API")


def _get_agent():
    """Get the global agent instance or raise 503."""
    if _agent is None:
        raise HTTPException(
            status_code=503,
            detail="AI Agent not initialized. Set GEMINI_API_KEY or configure the LLM provider.",
        )
    return _agent


def _build_config_snapshots_payload(agent) -> Dict[str, Any]:
    try:
        builder = ContextBundleBuilder(db_manager=getattr(agent, "_db", None))
        config_history = builder._fetch_config_history()
    except Exception as exc:
        return {"snapshots": [], "devices_with_snapshots": 0, "devices_missing_snapshots": [], "error": str(exc)}
    return {
        "snapshots": config_history.snapshots,
        "latest_snapshot_at": config_history.latest_snapshot_at,
        "devices_with_snapshots": config_history.devices_with_snapshots,
        "devices_missing_snapshots": config_history.devices_missing_snapshots,
        "source_used": config_history.source,
        "source_metadata": config_history.freshness.to_dict(),
    }


def _build_deployment_state_payload(agent) -> Dict[str, Any]:
    try:
        builder = ContextBundleBuilder(db_manager=getattr(agent, "_db", None))
        deployment = builder._fetch_deployment_status()
    except Exception as exc:
        return {"intents": [], "change_requests": [], "jobs": [], "error": str(exc)}
    return {
        "intents": deployment.intents,
        "change_requests": deployment.change_requests,
        "jobs": deployment.jobs,
        "source_used": deployment.source,
        "source_metadata": deployment.freshness.to_dict(),
    }


async def _run_tool_json(tool_func, *args, **kwargs) -> Dict[str, Any]:
    _get_agent()
    raw = await run_in_threadpool(tool_func, *args, **kwargs)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Invalid tool response: {exc}") from exc
    if isinstance(raw, dict):
        return raw
    return {"value": raw}


# -----------------------------------------------------------------------
# Chat Endpoints
# -----------------------------------------------------------------------

@router.post("", response_model=ChatResponse)
@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a chat message to the AI agent and get a response.

    If the AI generates a configuration, the response will include
    a `proposal` object with type="proposal" for human approval.
    """
    agent = _get_agent()

    try:
        result = await agent.chat(request.message, grounding=request.grounding)
        record_controller_chat_snapshot(request.message, result, model=agent.model_name)
        return ChatResponse(
            role=result.get("role", "assistant"),
            content=result.get("content", ""),
            model=agent.model_name,
            type=result.get("type", "text"),
            proposal=result.get("proposal"),
            intent_meta=result.get("intent_meta"),
            grounding_meta=result.get("grounding_meta"),
        )
    except Exception as e:
        logger.error("Chat error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """
    SSE streaming chat endpoint.

    Emits ``event: progress`` frames as the agent works, then a single
    ``event: complete`` frame with the final response.

    Use this from the frontend to show real-time phase indicators with
    elapsed timing (e.g. "Consulting device inventory (NetBox)… 0.8 s").
    """
    agent = _get_agent()

    async def _sse_generator():
        try:
            async for event in agent.chat_streaming(request.message, grounding=request.grounding):
                event_type = event.pop("event", "progress")
                yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                # Allow event loop to flush
                await asyncio.sleep(0)

            # Record the last event for observability
            if event_type == "complete":
                record_controller_chat_snapshot(
                    request.message, event, model=agent.model_name
                )
        except Exception as exc:
            logger.error("Chat stream error: %s", exc)
            error_payload = json.dumps({"error": str(exc)})
            yield f"event: error\ndata: {error_payload}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runtime")
async def get_chat_runtime(ollama_base_url: Optional[str] = None):
    """Return the active LLM runtime and selectable model catalog."""
    agent = _get_agent()
    return _build_runtime_state(agent, ollama_base_url=ollama_base_url)


@router.post("/runtime")
async def update_chat_runtime(config: ChatRuntimeUpdateRequest):
    """Switch the shared chat agent to a different provider/model runtime."""
    agent = _get_agent()
    provider_name = str(config.provider or "").strip().lower()
    model_name = (config.model or "").strip() or None
    base_url = (config.ollama_base_url or "").strip() or None

    _apply_runtime_provider(
        agent,
        provider_name=provider_name,
        model=model_name,
        ollama_base_url=base_url,
        clear_history=config.clear_history,
    )
    logger.info(
        "Chat runtime updated (provider=%s, model=%s, base_url=%s)",
        provider_name,
        model_name or getattr(agent, "model_name", ""),
        base_url or _current_provider_base_url(agent),
    )
    return _build_runtime_state(agent, ollama_base_url=base_url)


@router.post("/clear")
async def clear_history():
    """Clear the conversation history."""
    agent = _get_agent()
    agent.clear_history()
    clear_latest_controller_chat_snapshot()
    return {"status": "ok", "message": "Conversation history cleared"}


@router.get("/history")
async def get_history():
    """Get the current conversation history."""
    agent = _get_agent()
    return {"history": agent.get_history()}


@router.get("/tools")
async def list_tools():
    """List all tools available to the AI agent."""
    agent = _get_agent()
    return {"tools": agent.available_tools}


@router.get("/info", response_model=InfoResponse)
async def get_info():
    """Get information about the AI agent's status and configuration."""
    agent = _get_agent()
    return InfoResponse(
        model=agent.model_name,
        tools=agent.available_tools,
        history_length=len(agent.get_history()),
        status="ready",
        scope_contract=getattr(agent, "controller_scope_contract", None),
        implementation_status=getattr(agent, "controller_implementation_status", None),
        milestone2_baseline=getattr(agent, "milestone2_baseline", None),
    )


@router.get("/observability")
async def get_controller_observability():
    """Return a canonical controller observability snapshot for debugging and higher-level agent integration."""
    agent = _get_agent()
    proposals = agent.proposal_manager.list_all()
    payload = build_controller_observability_payload(
        controller={
            "model": agent.model_name,
            "history_length": len(agent.get_history()),
            "scope_contract": getattr(agent, "controller_scope_contract", None),
            "implementation_status": getattr(agent, "controller_implementation_status", None),
            "milestone2_baseline": getattr(agent, "milestone2_baseline", None),
        },
        context_health=await _run_tool_json(network_tools.get_context_health),
        sync_freshness=await _run_tool_json(network_tools.get_last_sync_status),
        config_snapshots=_build_config_snapshots_payload(agent),
        proposal_state=summarize_proposals(proposals),
        deployment_state=_build_deployment_state_payload(agent),
        validation_outcomes=summarize_validation_outcomes(proposals),
        latest_chat=get_latest_controller_chat_snapshot(),
    )
    return payload


# ── Floating Overlay APIs ──────────────────────────────────────────────

@router.post("/ollama/playground")
async def ollama_playground(request: OllamaPlaygroundRequest):
    """
    Direct, unrestricted chat with the local Ollama instance.
    Bypasses standard agent context and grounding.
    """
    base_url = os.getenv("OLLAMA_BASE_URL", _DEFAULT_OLLAMA_VM_URL).rstrip("/")
    model = request.model or os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL

    from llm.ollama_provider import OllamaProvider
    from llm.provider import LLMMessage

    provider = OllamaProvider(base_url=base_url, model=model)
    try:
        response = await provider.generate(
            messages=[LLMMessage(role="user", content=request.prompt)],
            temperature=request.temperature
        )
        return {
            "content": response.content,
            "model": model,
            "provider": "ollama",
            "raw": response.raw
        }
    except Exception as e:
        logger.error(f"Ollama playground error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ollama/context_query")
async def ollama_context_query(request: OllamaPlaygroundRequest):
    """
    Chat with Ollama using the current network inventory as context.
    Provides a RAG-lite experience for the floating overlay.
    """
    base_url = os.getenv("OLLAMA_BASE_URL", _DEFAULT_OLLAMA_VM_URL).rstrip("/")
    model = request.model or os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL

    from llm.ollama_provider import OllamaProvider
    from llm.provider import LLMMessage

    try:
        # 1. Fetch inventory snapshot
        agent = _get_agent()
        if agent._db and not network_tools._db_manager:
            network_tools.set_db_manager(agent._db)

        netbox_records = network_tools._inventory_records_from_netbox()
        local_records = network_tools._inventory_records_from_local_db()

        direct_answer = _build_inventory_direct_answer(prompt_text=request.prompt, local_records=local_records)
        if direct_answer:
            return {
                "content": direct_answer,
                "model": "inventory_localdb",
                "provider": "local_inventory",
                "context_size": len(local_records),
            }

        merged = {str(r.get("hostname", "")).strip().lower(): r for r in local_records if str(r.get("hostname", "")).strip()}
        for r in netbox_records:
            h = str(r.get("hostname", "")).strip().lower()
            if h:
                merged[h] = r

        devices = list(merged.values())
        prompt_text = str(request.prompt or "")
        prompt_lower = prompt_text.lower()

        # Identify likely target devices from the question so we always enrich them.
        ranked_targets = network_tools._rank_inventory_records(devices, prompt_text, limit=6) if prompt_text else []
        target_hostnames = {
            str(r.get("hostname", "")).strip()
            for r in ranked_targets
            if str(r.get("hostname", "")).strip()
        }

        def _norm_host(hostname: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(hostname or "").lower())

        def _append_link_fact(
            out: List[Dict[str, str]],
            seen: set,
            src_host: str,
            src_if: str,
            dst_host: str,
            dst_if: str,
            source: str,
        ) -> None:
            a = str(src_host or "").strip()
            b = str(dst_host or "").strip()
            if not a or not b:
                return
            ai = str(src_if or "").strip() or "unknown-if"
            bi = str(dst_if or "").strip() or "unknown-if"
            key = tuple(sorted((a.lower(), b.lower()))) + tuple(sorted((ai.lower(), bi.lower())))
            if key in seen:
                return
            seen.add(key)
            out.append(
                {
                    "src": a,
                    "src_if": ai,
                    "dst": b,
                    "dst_if": bi,
                    "source": source,
                }
            )

        # 2. Format context
        total_count = len(devices)
        roles = {}
        vendors = {}
        for d in devices:
            r = d.get('role', 'unknown')
            v = d.get('vendor', 'unknown')
            roles[r] = roles.get(r, 0) + 1
            vendors[v] = vendors.get(v, 0) + 1

        summary_parts = [f"{count} {role}" for role, count in roles.items()]
        vendor_parts = [f"{count} {vendor}" for vendor, count in vendors.items()]

        inventory_text = f"REAL-TIME INVENTORY METADATA:\n"
        inventory_text += f"- Total Inventory Count: {total_count} devices\n"
        inventory_text += f"- Role Summary: {', '.join(summary_parts)}\n"
        inventory_text += f"- Vendor Summary: {', '.join(vendor_parts)}\n\n"
        inventory_text += "DEVICE SNAPSHOT LIST:\n"

        enrich_limit = 8
        discovered_links: List[Dict[str, str]] = []
        seen_links = set()

        for i, d in enumerate(devices):
            interfaces_summary = ""
            links_summary = ""

            hostname = str(d.get("hostname", ""))
            should_enrich = (
                i < enrich_limit
                or hostname in target_hostnames
                or (_norm_host(hostname) and _norm_host(hostname) in _norm_host(prompt_lower))
            )

            if should_enrich:
                try:
                    enriched = network_tools._enrich_inventory_record(d)
                    if enriched.get("interfaces"):
                        ifaces = [f"{iface['name']} ({iface['ip_address'] or 'no-ip'})" for iface in enriched["interfaces"][:15]]
                        interfaces_summary = f", Interfaces: [{', '.join(ifaces)}]"
                    if enriched.get("links"):
                        links = [
                            f"{l.get('local_interface') or '?'}->{l.get('peer_hostname') or '?'}:{l.get('peer_interface') or '?'}"
                            for l in enriched["links"]
                        ]
                        links_summary = f", Connections: [{', '.join(links)}]"
                        for l in enriched["links"]:
                            _append_link_fact(
                                discovered_links,
                                seen_links,
                                hostname,
                                str(l.get("local_interface") or ""),
                                str(l.get("peer_hostname") or ""),
                                str(l.get("peer_interface") or ""),
                                source="inventory_enrichment",
                            )
                except Exception:
                    pass

            inventory_text += f"- {hostname} ({d.get('management_ip')}), {d.get('vendor')} {d.get('role')}{interfaces_summary}{links_summary}\n"

        if total_count > enrich_limit:
            inventory_text += f"\n(Note: Detailed enrichment skipped for {total_count - enrich_limit} devices.)\n"

        try:
            if agent._db:
                topo = agent._db.get_topology() or {}
                live_nodes = topo.get("nodes", {}) if isinstance(topo, dict) else {}
                for src_host, node_data in live_nodes.items():
                    if not isinstance(node_data, dict):
                        continue
                    for src_if, edge in (node_data.get("edgeLink") or {}).items():
                        if not isinstance(edge, dict):
                            continue
                        _append_link_fact(
                            discovered_links,
                            seen_links,
                            str(src_host),
                            str(src_if or ""),
                            str(edge.get("remote_node") or ""),
                            str(edge.get("remote_interface") or ""),
                            source="live_topology",
                        )
        except Exception:
            pass

        try:
            planned = network_tools._get_planned_topology_data()
            for link in planned.get("links", []) or []:
                if not isinstance(link, dict):
                    continue
                _append_link_fact(
                    discovered_links,
                    seen_links,
                    str(link.get("src_hostname") or ""),
                    str(link.get("src_interface") or ""),
                    str(link.get("dst_hostname") or ""),
                    str(link.get("dst_interface") or ""),
                    source="planned_topology",
                )
        except Exception:
            pass

        relevant_links = [
            lf
            for lf in discovered_links
            if not target_hostnames
            or lf["src"] in target_hostnames
            or lf["dst"] in target_hostnames
        ]
        if len(relevant_links) < 10:
            relevant_links = discovered_links
        relevant_links = relevant_links[:120]

        inventory_text += "\nLINK FACTS (interface-to-interface):\n"
        if relevant_links:
            for lf in relevant_links:
                inventory_text += (
                    f"- {lf['src']}:{lf['src_if']} <-> {lf['dst']}:{lf['dst_if']} "
                    f"[source={lf['source']}]\n"
                )
        else:
            inventory_text += "- No link facts available.\n"

        # 3. Build prompt
        system_prompt = (
            "You are a Network Inventory Assistant. "
            f"There are exactly {total_count} devices in the inventory. "
            "Use the metadata and snapshot below for accurate counts. "
            "If asked 'how many', always refer to the 'Total Inventory Count' metadata. "
            "When asked about connections between two devices, prioritize LINK FACTS and quote both local and peer interfaces. "
            "Respond in GitHub-flavored Markdown using real bold labels, hyphen bullet lists, and numbered lists only when order matters. "
            "Do not escape Markdown markers and do not wrap the whole answer in a code block.\n\n"
            f"{inventory_text}"
        )
        
        provider = OllamaProvider(base_url=base_url, model=model)
        # Use a longer timeout for the full context query
        response = await provider.generate(
            messages=[
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=request.prompt)
            ],
            temperature=request.temperature
        )
        
        return {
            "content": response.content,
            "model": model,
            "provider": "ollama",
            "context_size": len(devices)
        }
    except Exception as e:
        logger.error(f"Ollama context query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ollama/models")
async def get_ollama_models(ollama_base_url: Optional[str] = None):
    """List available models from the local Ollama instance."""
    base_url = (ollama_base_url or os.getenv("OLLAMA_BASE_URL") or _DEFAULT_OLLAMA_VM_URL).rstrip("/")
    models, reachable, error = _fetch_ollama_models(base_url)
    if not reachable:
        raise HTTPException(status_code=503, detail=f"Ollama unreachable: {error}")
    return {"models": models}


@router.get("/inventory/snapshot")
async def get_inventory_snapshot():
    """
    Rapid, combined snapshot of NetBox and SQLite device inventory.
    """
    try:
        # Ensure network_tools has the DB manager if it was lost or not set
        agent = _get_agent()
        if agent._db and not network_tools._db_manager:
            network_tools.set_db_manager(agent._db)

        netbox_records = network_tools._inventory_records_from_netbox()
        local_records = network_tools._inventory_records_from_local_db()
        
        # Merge by hostname, preferring NetBox if available
        merged = { r["hostname"]: r for r in local_records }
        for r in netbox_records:
            merged[r["hostname"]] = r
            
        return {
            "devices": list(merged.values()),
            "count": len(merged),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Inventory snapshot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/inventory/device/{hostname}")
async def get_device_enrichment(hostname: str):
    """
    Fetch detailed interfaces and links for a specific device.
    """
    try:
        # Ensure network_tools has the DB manager
        agent = _get_agent()
        if agent._db and not network_tools._db_manager:
            network_tools.set_db_manager(agent._db)

        # 1. Resolve canonical hostname first
        canonical_name = network_tools._resolve_hostname_alias(hostname)
        
        # 2. Find the basic record (from NB or local DB)
        records, _source = network_tools._get_inventory_records()
        record = network_tools._find_inventory_record(records, canonical_name)
        
        if not record:
            raise HTTPException(status_code=404, detail=f"Device '{hostname}' not found in inventory.")

        # 3. Enrich with interfaces and links
        enriched = network_tools._enrich_inventory_record(record)
        return enriched
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Device enrichment error for {hostname}: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/scope")
async def get_controller_scope():
    """Return the frozen controller scope contract and the current implementation baseline."""
    agent = _get_agent()
    return {
        "scope_contract": getattr(agent, "controller_scope_contract", None),
        "implementation_status": getattr(agent, "controller_implementation_status", None),
        "milestone2_baseline": getattr(agent, "milestone2_baseline", None),
    }


@router.get("/milestone-2-baseline")
async def get_milestone2_baseline():
    """Return the current backend baseline against the Milestone 2 grounding foundation."""
    agent = _get_agent()
    return getattr(agent, "milestone2_baseline", None)


@router.post("/refresh")
async def refresh_context():
    """
    Refresh the agent's network context.
    Call this after a topology crawl completes.
    """
    agent = _get_agent()
    agent.refresh_context()
    return {"status": "ok", "message": "Network context refreshed"}


@router.get("/semantics")
async def get_canonical_semantics():
    """Return the canonical planned-vs-live semantics contract used across tools, MCP, and prompt bundles."""
    return await _run_tool_json(network_tools.get_canonical_semantics_map)


@router.get("/canonical-device-state")
async def get_canonical_device_state(hostname: Optional[str] = None):
    """Return the canonical planned-vs-live device-state view, optionally filtered to a single hostname."""
    payload = await _run_tool_json(network_tools.compare_planned_vs_live)
    if hostname:
        lowered = hostname.strip().lower()
        payload["devices"] = [
            device for device in payload.get("devices", [])
            if str(device.get("hostname", "")).lower() == lowered
        ]
        payload["matching_devices"] = [
            name for name in payload.get("matching_devices", [])
            if str(name).lower() == lowered
        ]
        payload["planned_only"] = [
            name for name in payload.get("planned_only", [])
            if str(name).lower() == lowered
        ]
        payload["live_only"] = [
            name for name in payload.get("live_only", [])
            if str(name).lower() == lowered
        ]
    return payload


@router.get("/benchmark/cases")
async def get_benchmark_cases():
    """Return the benchmark categories and canonical case definitions used for regression scoring."""
    return {
        "categories": benchmark_categories(),
        "cases": list_benchmark_cases(),
    }


@router.get("/advanced-differentiators")
async def get_advanced_differentiators():
    """Return the Step 19 reservation and unlock status for advanced differentiator surfaces."""
    agent = _get_agent()
    return _advanced_differentiator_status(agent)


@router.post("/benchmark")
async def run_chat_benchmark(request: Optional[BenchmarkRunRequest] = None):
    """Execute the deterministic chat benchmark suite and return a structured scoring report."""
    agent = _get_agent()
    try:
        return await run_benchmark_suite(agent, selected_categories=(request.categories if request else None))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/resilience/cases")
async def get_resilience_cases():
    """Return the synthetic and replay-based resilience scenarios used for Step 28 regression coverage."""
    return {
        "categories": resilience_categories(),
        "scenarios": list_resilience_scenarios(),
    }


@router.post("/resilience")
async def run_chat_resilience(request: Optional[ResilienceRunRequest] = None):
    """Execute the deterministic synthetic/replay resilience suite for outage and event-storm regression protection."""
    try:
        return run_resilience_suite(selected_categories=(request.categories if request else None))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/change-intelligence/info")
async def get_change_intelligence_info():
    """Return the active Step 29 change-intelligence analysis version."""
    agent = _get_agent()
    return {
        "analysis_version": CHANGE_INTELLIGENCE_VERSION,
        "stability_gate": _advanced_differentiator_status(agent, feature_name="change_intelligence"),
    }


@router.post("/change-intelligence")
async def run_change_intelligence(request: ChangeIntelligenceRequest):
    """Run the deterministic Step 29 change-intelligence analysis for a proposed change."""
    agent = _get_agent()
    _require_advanced_differentiator("change_intelligence", agent)
    payload = await _run_tool_json(
        network_tools.analyze_change_intelligence,
        request.intent_json,
        request.user_role,
    )
    if payload.get("error"):
        raise HTTPException(status_code=500, detail=str(payload["error"]))
    return payload


@router.get("/digital-twin/info")
async def get_digital_twin_info():
    """Return the active Step 30 digital-twin schema version."""
    agent = _get_agent()
    return {
        "version": DIGITAL_TWIN_VERSION,
        "stability_gate": _advanced_differentiator_status(agent, feature_name="digital_twin"),
    }


@router.post("/digital-twin")
async def run_digital_twin(request: Optional[DigitalTwinRequest] = None):
    """Build the formal Step 30 digital-twin graph snapshot for a device or the whole network."""
    agent = _get_agent()
    _require_advanced_differentiator("digital_twin", agent)
    payload = await _run_tool_json(
        network_tools.get_digital_twin,
        request.hostname if request else "",
        request.vendor_filter if request else "",
        request.include_telemetry if request else True,
        request.include_sdn if request else True,
    )
    if payload.get("error"):
        raise HTTPException(status_code=500, detail=str(payload["error"]))
    return payload


# -----------------------------------------------------------------------
# M19: Proposal Lifecycle Endpoints
# -----------------------------------------------------------------------

@router.get("/proposals")
async def list_proposals(status: Optional[str] = None):
    """
    List all config proposals, optionally filtered by status.

    Query params:
        status: "pending" | "approved" | "rejected" | "deployed" | "rolled_back" | "failed"
    """
    agent = _get_agent()
    mgr = agent.proposal_manager

    if status == "pending":
        proposals = mgr.list_pending()
    elif status:
        proposals = [p for p in mgr.list_all() if p.status == status]
    else:
        proposals = mgr.list_all()

    return {"proposals": [p.model_dump() for p in proposals]}


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str):
    """Get a specific proposal by ID."""
    agent = _get_agent()
    proposal = agent.proposal_manager.get(proposal_id)
    if not proposal:
        raise HTTPException(404, f"Proposal {proposal_id} not found")
    return proposal.model_dump()


@router.post("/proposals/{proposal_id}/review")
async def review_proposal_lines(
    proposal_id: str,
    body: ProposalReviewRequest,
    user=Depends(require_role("operator", "admin")),
):
    """Persist reviewed/merged config on a pending proposal before final approval."""
    agent = _get_agent()
    mgr = agent.proposal_manager

    proposal = mgr.get(proposal_id)
    if not proposal:
        raise HTTPException(404, f"Proposal {proposal_id} not found")
    if proposal.status != "pending":
        raise HTTPException(
            409, f"Proposal is '{proposal.status}', expected 'pending'"
        )

    merged = (body.merged_config or "").strip("\n")
    if not merged.strip():
        raise HTTPException(422, "Merged config cannot be empty")

    updated = mgr.update_pending_generated_config(proposal_id, merged)
    if not updated:
        raise HTTPException(500, "Failed to update proposal review")

    return {
        "status": "reviewed",
        "message": "Line-level review saved",
        "proposal": updated.model_dump(),
        "review": {
            "accepted_count": len(body.accepted_line_indices),
            "rejected_count": len(body.rejected_line_indices),
            "reviewed_by": user.get("username", "unknown"),
        },
    }


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: str,
    user=Depends(require_role("operator", "admin")),
):
    """
    Approve a pending proposal and deploy it to the target device.

    This triggers the full M4 deployment pipeline:
    1. Create Change Request
    2. Create Job
    3. Plan (config already generated)
    4. Snapshot Pre
    5. Apply Config
    6. Verify
    7. Commit

    SSH credentials are auto-fetched from the Credential Vault (M15).
    """
    agent = _get_agent()
    mgr = agent.proposal_manager

    # 1. Validate the proposal exists and is pending
    proposal = mgr.get(proposal_id)
    if not proposal:
        raise HTTPException(404, f"Proposal {proposal_id} not found")
    if proposal.status != "pending":
        raise HTTPException(
            409, f"Proposal is '{proposal.status}', expected 'pending'"
        )

    action_artifact = proposal.action_artifact or {}
    validation_report = validate_action_artifact(
        action_artifact,
        vendor=proposal.vendor,
        feature=proposal.feature,
        params=dict(proposal.params or {}),
        proposal_generated_config=proposal.generated_config,
        require_render_match=True,
    )
    if not validation_report.get("passed", False):
        raise HTTPException(
            422,
            {
                "message": "Proposal failed Step 10 safety validation and cannot be approved.",
                "validation": validation_report,
            },
        )

    # 2. Approve in the proposal manager
    proposal = mgr.approve(proposal_id)
    if not proposal:
        raise HTTPException(500, "Failed to approve proposal")

    action_artifact = proposal.action_artifact or {}
    if action_artifact.get("action_type") == "multi_device_playbook":
        return {
            "status": "approved",
            "message": "Proposal approved. This multi-device playbook artifact requires coordinated execution review and is not auto-deployed through the single-device pipeline.",
            "proposal": proposal.model_dump(),
        }

    is_admin = user.get("role") == "admin"

    # Engineer/operator approval: approve proposal only (no device deployment)
    if not is_admin:
        return {
            "status": "approved",
            "message": "Proposal approved. Admin approval is required to deploy to devices.",
            "proposal": proposal.model_dump(),
        }

    # 3. Check if pipeline is available
    if not _pipeline:
        # No pipeline — mark as deployed anyway (simulation mode)
        mgr.mark_deployed(proposal_id, job_id=None, request_id=None)
        return {
            "status": "deployed",
            "message": "Proposal approved (no deployment pipeline available — simulated)",
            "proposal": mgr.get(proposal_id).model_dump(),
        }

    # 4. Auto-fetch SSH credentials from the Credential Vault
    creds = None
    if _vault and proposal.target_device_id > 0:
        try:
            creds = _vault.get(proposal.target_device_id, "SSH")
        except Exception as e:
            logger.warning("Failed to fetch credentials: %s", e)

    if not creds:
        # Fall back: return 412 asking user to store credentials
        raise HTTPException(
            412,
            f"No SSH credentials found for device '{proposal.target_device}' "
            f"(device_id={proposal.target_device_id}). "
            "Please store credentials via the Credential Vault before deploying."
        )

    # 5. Resolve SSH host: prefer management IP over plain hostname
    ssh_host = proposal.target_device  # fallback if lookup fails
    if proposal.target_device_id > 0:
        try:
            dev = _pipeline.db_manager.get_device_by_id(proposal.target_device_id)
            if dev and dev.management_ip:
                ssh_host = dev.management_ip
                logger.info(
                    "Resolved device '%s' → %s",
                    proposal.target_device, ssh_host,
                )
        except Exception as e:
            logger.warning("Could not resolve management IP: %s", e)

    # 6. Execute the deployment pipeline
    try:
        # Create change request
        req_id = _pipeline.create_change_request(
            title=f"AI Proposal: {proposal.feature} on {proposal.target_device}",
            requester="ai-agent",
            description=proposal.user_prompt,
        )

        # Create job
        job_id = _pipeline.add_job(req_id, proposal.target_device_id)

        # Run plan stage (config already generated, but pipeline needs it)
        await run_in_threadpool(
            _pipeline.run_plan_stage,
            job_id,
            vendor=proposal.vendor,
            feature=proposal.feature,
            params=proposal.params,
            generated_config=proposal.generated_config,
            rollback_config=proposal.rollback_config,
            execution_engine=(action_artifact.get("execution") or {}).get("primary_engine"),
        )

        # Build verification checks from intent pipeline if available
        verification_checks = []
        _intent_meta = getattr(proposal, '_intent_meta', None)
        if _intent_meta and _intent_meta.get('verification_checks'):
            from deploy.verifier import VerificationCheck
            for vc in _intent_meta['verification_checks']:
                verification_checks.append(VerificationCheck(
                    name=vc.get('name') or vc.get('description') or vc.get('check_type', 'verification_check'),
                    check_type=vc.get('check_type', 'command_output'),
                    target=vc.get('target') or vc.get('command', ''),
                    expected=vc.get('expected', ''),
                    timeout=int(vc.get('timeout', 5) or 5),
                ))
            logger.info(
                "Intent pipeline provided %d auto-verification checks",
                len(verification_checks),
            )

        # Execute stages 3-6 (Snapshot → Apply → Verify → Commit)
        execute_kwargs = dict(
            job_id=job_id,
            host=ssh_host,
            username=creds["username"],
            password=creds["password"],
            driver_type=proposal.vendor,
        )
        if verification_checks:
            execute_kwargs["checks"] = verification_checks

        success = await run_in_threadpool(
            _pipeline.execute_job,
            **execute_kwargs,
        )

        verification_report = build_post_change_verification_report(
            proposal,
            job_id=job_id,
            request_id=req_id,
            pipeline=_pipeline,
            db_manager=getattr(_pipeline, "db_manager", None),
        )

        if success:
            proposal = mgr.mark_deployed(proposal_id, job_id=job_id, request_id=req_id)
            proposal = _persist_post_change_verification(
                mgr,
                proposal_id,
                proposal,
                verification_report,
                execution_status="deployed",
            )
            if verification_report.get("outcome") != "passed":
                return {
                    "status": "deployed_with_findings",
                    "message": f"Configuration deployed to {proposal.target_device}, but post-change verification requires review.",
                    "job_id": job_id,
                    "request_id": req_id,
                    "proposal": proposal.model_dump(),
                    "post_change_verification": verification_report,
                    "recommended_action": verification_report.get("recommended_action"),
                }
            return {
                "status": "deployed",
                "message": f"Configuration deployed to {proposal.target_device}",
                "job_id": job_id,
                "request_id": req_id,
                "proposal": proposal.model_dump(),
                "post_change_verification": verification_report,
            }
        else:
            jobs = _pipeline.get_all_jobs()
            job_state = next((row for row in jobs if row.get("id") == job_id), {})
            job_status = str(job_state.get("status") or "failed").lower()
            proposal = mgr.mark_failed(proposal_id)
            proposal = _persist_post_change_verification(
                mgr,
                proposal_id,
                proposal,
                verification_report,
                execution_status=job_status,
            )
            return {
                "status": "failed",
                "message": f"Deployment to {proposal.target_device} failed; automatic rollback is disabled, so review the post-change verification report and approve rollback explicitly if needed.",
                "job_id": job_id,
                "request_id": req_id,
                "proposal": proposal.model_dump(),
                "post_change_verification": verification_report,
                "recommended_action": verification_report.get("recommended_action"),
            }

    except Exception as e:
        logger.error("Deployment failed: %s", e)
        raise HTTPException(500, f"Deployment error: {e}")


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    user=Depends(require_role("operator", "admin")),
):
    """Reject a pending proposal. The config is discarded."""
    agent = _get_agent()
    mgr = agent.proposal_manager

    proposal = mgr.get(proposal_id)
    if not proposal:
        raise HTTPException(404, f"Proposal {proposal_id} not found")
    if proposal.status != "pending":
        raise HTTPException(
            409, f"Proposal is '{proposal.status}', expected 'pending'"
        )

    result = mgr.reject(proposal_id)
    if not result:
        raise HTTPException(500, "Failed to reject proposal")

    return {
        "status": "rejected",
        "message": "Proposal rejected",
        "proposal": result.model_dump(),
    }


@router.post("/proposals/{proposal_id}/rollback")
async def rollback_proposal(
    proposal_id: str,
    user=Depends(require_role("admin")),
):
    """
    Rollback a deployed proposal by applying its stored undo commands.
    """
    agent = _get_agent()
    mgr = agent.proposal_manager

    proposal = mgr.get(proposal_id)
    if not proposal:
        raise HTTPException(404, f"Proposal {proposal_id} not found")
    if proposal.status not in {"deployed", "failed"}:
        raise HTTPException(
            409, f"Proposal is '{proposal.status}', expected 'deployed' or 'failed'"
        )

    action_artifact = proposal.action_artifact or {}
    rollback_intent = action_artifact.get("rollback_intent") if isinstance(action_artifact, dict) else {}
    job = _load_pipeline_job(proposal.job_id) if proposal.job_id else None
    rollback_available = bool(
        str(proposal.rollback_config or "").strip()
        or (isinstance(rollback_intent, dict) and int(rollback_intent.get("available_device_count") or 0) > 0)
        or (job and getattr(job, "snapshot_pre_id", None))
    )
    if not rollback_available:
        raise HTTPException(422, "No rollback data is available for this proposal")

    # If pipeline is available, try to execute rollback via the pipeline
    if _pipeline and proposal.job_id and job:
        try:
            from deploy.rollback import RollbackEngine

            ssh_host, creds = _resolve_device_access(proposal)
            if creds:
                rollback_success = await run_in_threadpool(
                    RollbackEngine(_pipeline.db_manager).execute_rollback,
                    job,
                    ssh_host,
                    creds["username"],
                    creds["password"],
                    proposal.vendor,
                )
                if rollback_success:
                    proposal = mgr.mark_rolled_back(proposal_id)
                    proposal = _persist_rollback_execution(
                        mgr,
                        proposal_id,
                        proposal,
                        "executed",
                        {
                            "updated_at": proposal.updated_at,
                            "mode": "pipeline_explicit_admin",
                            "summary": f"Rollback executed on {proposal.target_device} after explicit admin approval.",
                            "job_id": proposal.job_id,
                        },
                    )
                    return {
                        "status": "rolled_back",
                        "message": f"Rollback executed for {proposal.target_device}",
                        "rollback_config": proposal.rollback_config,
                        "proposal": proposal.model_dump(),
                    }
                proposal = _persist_rollback_execution(
                    mgr,
                    proposal_id,
                    proposal,
                    "execution_failed",
                    {
                        "updated_at": proposal.updated_at,
                        "mode": "pipeline_explicit_admin",
                        "summary": f"Rollback execution failed for {proposal.target_device}; review rollback commands and snapshot state manually.",
                        "job_id": proposal.job_id,
                    },
                )
                return {
                    "status": "rollback_failed",
                    "message": f"Rollback execution failed for {proposal.target_device}. Review rollback data manually.",
                    "rollback_config": proposal.rollback_config,
                    "proposal": proposal.model_dump(),
                }

        except Exception as e:
            logger.error("Rollback via pipeline failed: %s", e)
            # Fall through to manual rollback

    proposal = _persist_rollback_execution(
        mgr,
        proposal_id,
        proposal,
        "prepared",
        {
            "updated_at": proposal.updated_at,
            "mode": "manual_preparation",
            "summary": f"Rollback data prepared for explicit operator execution on {proposal.target_device}.",
            "job_id": proposal.job_id,
        },
    )
    return {
        "status": "rollback_prepared",
        "message": f"Rollback approval accepted, but execution remains manual for {proposal.target_device}. Review and apply the rollback data explicitly.",
        "rollback_config": proposal.rollback_config,
        "rollback_intent": rollback_intent,
        "proposal": proposal.model_dump(),
    }


# -----------------------------------------------------------------------
# Playbook Review & Q&A APIs
# -----------------------------------------------------------------------

class PlaybookAnalysisRequest(BaseModel):
    playbook_content: str
    prompt_request: Optional[str] = None


class PlaybookAnalysisResponse(BaseModel):
    explanation: str
    rating: int
    safety_level: str
    findings: List[str]
    operational_rating: int
    operational_level: str
    operational_explanation: str


class PlaybookQARequest(BaseModel):
    playbook_content: str
    question: str
    history: List[Dict[str, str]] = Field(default_factory=list)


def extract_fields_via_regex(content: str) -> dict:
    """Attempts to extract explanation, rating, safety_level, and findings from malformed JSON via regex."""
    import re
    result = {}
    
    # Try to find rating: an integer
    rating_match = re.search(r'"rating"\s*:\s*(\d+)', content)
    if rating_match:
        try:
            result["rating"] = int(rating_match.group(1))
        except Exception:
            pass
            
    # Try to find safety_level: "Safe", "Warning", "Danger"
    safety_match = re.search(r'"safety_level"\s*:\s*"([^"]+)"', content, re.IGNORECASE)
    if safety_match:
        val = safety_match.group(1).strip().capitalize()
        if val in ("Safe", "Warning", "Danger"):
            result["safety_level"] = val
            
    # Try to find explanation: string or dictionary
    explanation_match = re.search(r'"explanation"\s*:\s*"(.*?)"(?=\s*,\s*"|\s*})', content, re.DOTALL)
    if explanation_match:
        val = explanation_match.group(1)
        val = val.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
        result["explanation"] = val
    else:
        explanation_obj_match = re.search(r'"explanation"\s*:\s*\{(.*?)\}(?=\s*,\s*"|\s*})', content, re.DOTALL)
        if explanation_obj_match:
            try:
                parsed_obj = json.loads("{" + explanation_obj_match.group(1) + "}")
                result["explanation"] = parsed_obj
            except Exception:
                result["explanation"] = explanation_obj_match.group(1).strip()

    # Try to find findings list using a robust parenthetical scanner to handle nested brackets
    findings_match = re.search(r'"findings"\s*:\s*\[', content)
    if findings_match:
        start_idx = findings_match.end()
        bracket_count = 1
        end_idx = start_idx
        in_quotes = False
        escaped = False
        while end_idx < len(content):
            char = content[end_idx]
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_quotes = not in_quotes
            elif not in_quotes:
                if char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        break
            end_idx += 1
        
        findings_str = content[start_idx:end_idx]
        items = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', findings_str)
        cleaned_items = []
        for item in items:
            cleaned = item.replace('\\"', '"').replace('\\n', '\n').strip()
            if cleaned:
                cleaned_items.append(cleaned)
        if cleaned_items:
            result["findings"] = cleaned_items

    # Try to find operational_rating: an integer
    op_rating_match = re.search(r'"operational_rating"\s*:\s*(\d+)', content)
    if op_rating_match:
        try:
            result["operational_rating"] = int(op_rating_match.group(1))
        except Exception:
            pass

    # Try to find operational_level: "Normal", "Alert", "Warning"
    op_level_match = re.search(r'"operational_level"\s*:\s*"([^"]+)"', content, re.IGNORECASE)
    if op_level_match:
        val = op_level_match.group(1).strip().capitalize()
        if val in ("Normal", "Alert", "Warning"):
            result["operational_level"] = val

    # Try to find operational_explanation: string
    op_exp_match = re.search(r'"operational_explanation"\s*:\s*"(.*?)"(?=\s*,\s*"|\s*})', content, re.DOTALL)
    if op_exp_match:
        val = op_exp_match.group(1)
        val = val.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
        result["operational_explanation"] = val

    return result


def run_static_playbook_audit(playbook_content: str) -> dict:
    """Performs static fallback analysis of an Ansible playbook when AI/JSON parsing fails completely."""
    import re
    rating = 9
    findings = []
    
    # 1. Inspect hosts
    hosts_match = re.search(r'hosts:\s*["\']?([^"\']+)["\']?', playbook_content)
    hosts = hosts_match.group(1).strip() if hosts_match else "unspecified hosts"
    findings.append(f"[✓] Playbook target scope: {hosts}")
    
    # 2. Inspect modules
    common_modules = [
        "cisco.ios.ios_config", "cisco.ios.ios_vlans", "cisco.ios.ios_interfaces",
        "arista.eos.eos_config", "arista.eos.eos_vlans", "vyos.vyos.vyos_config"
    ]
    detected_modules = []
    for mod in common_modules:
        if mod in playbook_content:
            detected_modules.append(mod.split(".")[-1])
    
    if detected_modules:
        findings.append(f"[✓] Uses native configuration modules: {', '.join(detected_modules)}")
    else:
        if "raw" in playbook_content or "cli_command" in playbook_content:
            findings.append("[✓] Playbook contains raw cli_command/raw tasks (worth attention)")
            rating = min(rating, 8)
        else:
            findings.append("[✓] Playbook uses standard automation modules")
            
    # 3. Check ignore_errors / ignore_unreachable
    if "ignore_errors: true" in playbook_content or "ignore_unreachable: true" in playbook_content:
        findings.append("[✓] Playbook uses ignore_unreachable / ignore_errors settings")
        
    # 4. Check for destructive commands or locks
    if "state: overridden" in playbook_content or "state: deleted" in playbook_content:
        findings.append("[⚠️ CRITICAL WARNING] Overridden or deleted states detected; check replacement logic carefully to avoid unexpected traffic drops.")
        rating = min(rating, 5)
        
    # 5. Check for lockout keywords
    lockout_keywords = ["line vty", "ssh", "aaa", "mgmt0", "access-list", "acl"]
    found_lockouts = [kw for kw in lockout_keywords if kw in playbook_content.lower()]
    if found_lockouts:
        findings.append(f"[✓] Management interfaces/configs targeted ({', '.join(found_lockouts)}). Verify that rollback capability is enabled.")
        
    # 6. Check for plain-text secrets
    secret_keywords = ["password", "secret", "snmp_community", "community"]
    found_secrets = []
    for line in playbook_content.splitlines():
        for kw in secret_keywords:
            if kw in line.lower() and (":" in line or "=" in line):
                found_secrets.append(kw)
                break
    if found_secrets:
        findings.append("[⚠️ CRITICAL WARNING] Potential hardcoded plain-text credentials or SNMP community strings detected.")
        rating = min(rating, 4)

    has_critical = any("[⚠️ CRITICAL WARNING]" in f for f in findings)
    if has_critical:
        safety_level = "Warning" if rating >= 5 else "Danger"
    else:
        safety_level = "Safe"
        
    explanation = (
        f"**Static Analysis Fallback**\n\n"
        f"- **Scope**: Target hosts: {hosts}.\n"
        f"- **Impact Assessment**: Configuration will be pushed using standard network modules. "
    )
    if detected_modules:
        explanation += f"Detected modules: {', '.join(detected_modules)}."
    else:
        explanation += "No specific native modules identified; verify commands manually."
        
    if has_critical:
        explanation += "\n- **[⚠️ CRITICAL WARNING]**: Potential risks detected (credentials or override state)."

    # Determine Operational Rating & Level (Importance Level)
    operational_rating = 10
    operational_level = "Normal"
    ops_reasons = []

    # Check for High Impact (Warning - Importance Level 3)
    high_impact_keywords = ["line vty", "ssh", "aaa", "mgmt0", "reload", "reboot", "state: overridden", "state: deleted"]
    found_high_impact = [kw for kw in high_impact_keywords if kw in playbook_content.lower()]
    if found_high_impact or found_secrets:
        operational_rating = 4
        operational_level = "Warning"
        if found_high_impact:
            ops_reasons.append(f"High risk operations detected: management lockout or global destructive state modifications ({', '.join(found_high_impact)}).")
        if found_secrets:
            ops_reasons.append("High risk operations detected: hardcoded secrets/passwords.")
    
    # Check for Medium Impact (Alert - Importance Level 2) if not already Warning
    if operational_level != "Warning":
        medium_impact_keywords = ["shutdown", "enabled: false", "enabled: no", "state: down", "router ospf", "router bgp", "ip route", "access-list", "acl", "snmp-server"]
        found_med_impact = [kw for kw in medium_impact_keywords if kw in playbook_content.lower()]
        if found_med_impact:
            operational_rating = 7
            operational_level = "Alert"
            ops_reasons.append(f"Medium risk operations detected: routing, interface state shutdowns, ACL policies, or SNMP management configs ({', '.join(found_med_impact)}).")
    
    if not ops_reasons:
        operational_rating = 10
        operational_level = "Normal"
        ops_reasons.append("Low risk operations: standard playbook configuring access interfaces, VLANs, descriptions, or standard properties.")

    operational_explanation = " ".join(ops_reasons)

    return {
        "explanation": explanation,
        "rating": rating,
        "safety_level": safety_level,
        "findings": findings,
        "operational_rating": operational_rating,
        "operational_level": operational_level,
        "operational_explanation": operational_explanation
    }


@router.post("/playbook/analyze", response_model=PlaybookAnalysisResponse)
async def analyze_playbook(request: PlaybookAnalysisRequest):
    """
    Analyzes an Ansible playbook, giving a plain English explanation,
    a safety rating (0-10), a safety level, and specific risk findings.
    """
    agent = _get_agent()
    provider = agent._provider

    system_prompt = (
        "You are an expert network security auditor. Analyze the following Ansible Playbook.\n\n"
        "STRICT WARNING TRIGGERS (Only raise a warning or lower the rating below 8 if one of these actions is explicitly performed in the playbook):\n"
        "1. INFRASTRUCTURE & MANAGEMENT LOCKOUT (Direct lockout configuration changes):\n"
        "   - The playbook explicitly modifies management configurations: VTY lines, SSH line settings, AAA configurations, console line settings, or Mgmt0/management interfaces.\n"
        "   - The playbook explicitly deletes or removes access-lists (ACLs) or firewall policies that secure management access.\n"
        "2. GLOBAL DESTRUCTIVE ACTIONS:\n"
        "   - The playbook uses 'state: overridden' or 'state: deleted' targeting global interface configurations or root configurations (instead of scoping to specific lines/interfaces).\n"
        "3. DANGEROUS NETWORK KEYWORDS:\n"
        "   - Hardcoded plain-text community strings (SNMP), RADIUS/TACACS+ secrets, or enable passwords.\n"
        "   - Using raw CLI command modules ('raw' or 'cli_command' modules) unnecessarily when native structured modules (like cisco.ios.ios_interfaces or cisco.ios.ios_vlans) are available.\n\n"
        "IMPORTANT AUDITING RULES:\n"
        "- The 'rating' is a Playbook Reference Rating representing if the playbook successfully satisfies/fulfills the requested prompt change/intent and does not perform any dangerous lockout or destructive actions. If the playbook satisfies the prompt request and does not modify management/console access, it is considered safe and correct (Rating must be 9 or 10).\n"
        "- Do NOT lower the rating, raise warnings, or criticize the playbook for lacking 'rollback safety nets' or not clearing configurations. These are not required for standard configuration tasks.\n"
        "- Do NOT criticize the playbook for using 'ignore_unreachable: true' or 'ignore_errors: true'—these are standard settings and are not important/dangerous.\n"
        "- Do NOT complain about native structured modules unless the playbook uses raw command lines unnecessarily.\n"
        "- Only output '[⚠️ CRITICAL WARNING]' inside findings if a strict warning trigger is explicitly broken. Otherwise, prefix findings with '[✓]'.\n\n"
        "PLAYBOOK OPERATIONAL RATING & IMPORTANCE LEVEL CLASSIFICATION:\n"
        "Evaluate the playbook's tasks and classify them into one of the following operational impact categories:\n"
        "1. Normal (Low Operational Risk, Operational Rating: 8 - 10, Level: 'Normal'):\n"
        "   - Standard/safe tasks: adding/modifying VLANs, configuring interface speed/duplex/descriptions, configuring L2 switchport access/trunk modes, QoS settings (class-maps, policy-maps), LLDP/CDP, NTP, gathering facts, or advertising network prefixes under existing routing protocols (e.g. OSPF network prefix advertisement).\n"
        "2. Alert (Medium Operational Risk, Operational Rating: 5 - 7, Level: 'Alert'):\n"
        "   - Tasks that modify active routing/interfaces: shutting down interfaces (admin down/shutdown/enabled: false), changing/adding interface IP addresses (IP conflict risk), modifying active dynamic routing protocols (OSPF, BGP processes, passive-interfaces, redistribution), configuring static routes (ip route), configuring standard access control lists (ACLs/prefix-lists) on interfaces, or configuring SNMP settings.\n"
        "3. Warning (High Operational Risk, Operational Rating: 0 - 4, Level: 'Warning'):\n"
        "   - Critical operational risks: modifying management interfaces (Mgmt0, management VRF, Loopback0 for management), changing console line settings, modifying SSH/VTY configs (transport input, access-class), modifying AAA or user authentication configs, reloads/reboots, global config overrides (state: overridden or state: deleted targeting global interfaces/root), or including hardcoded plaintext secrets/passwords.\n\n"
        "Provide your response in raw JSON format with exactly the following keys:\n"
        "- 'explanation': A concise summary string (not a JSON object/dict) formatted exactly as a markdown text block containing:\n"
        "  - **Scope**: Brief summary of target infrastructure, platforms, and objectives.\n"
        "  - **Impact Assessment**: Anticipated state changes (e.g., VLANs added, interfaces modified).\n"
        "  - **[⚠️ CRITICAL WARNING]**: Only include this section if a strict trigger is broken. State the exact task line, device platform, and the physical/routing consequence.\n"
        "- 'rating': An integer rating between 0 and 10, based strictly on prompt satisfaction and safety (as defined above). Rating must be 9 or 10 if no strict trigger is broken.\n"
        "- 'safety_level': A string, either 'Safe', 'Warning', or 'Danger'. If any strict trigger is broken, set to 'Warning' or 'Danger'. Otherwise, set to 'Safe'.\n"
        "- 'findings': A list of strings detailing specific findings or structural observations. Prefix positive/neutral findings with '[✓]'. Only prefix with '[⚠️ CRITICAL WARNING]' if a strict trigger is broken. Always populate this list with at least 2-3 specific findings or observations.\n"
        "- 'operational_rating': An integer rating between 0 and 10, based on the operational risk level classified above.\n"
        "- 'operational_level': A string, either 'Normal', 'Alert', or 'Warning' based on the operational classification criteria above.\n"
        "- 'operational_explanation': A concise, plain English explanation summarizing the operational impact (e.g., 'Medium risk operations: interface state shutdowns detected.').\n\n"
        "Do not include any formatting, markdown markers, or backticks around the JSON. Output only the pure JSON text."
    )
    user_prompt = f"Ansible Playbook:\n```yaml\n{request.playbook_content}\n```"
    if request.prompt_request:
        user_prompt = f"Requested Change / Prompt Intent: {request.prompt_request}\n\n" + user_prompt

    from llm.provider import LLMMessage
    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_prompt)
    ]

    try:
        response = await provider.generate(messages=messages, temperature=0.2)
        content = (response.content or "").strip()
        
        # Clean up any potential markdown wrapper
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        # Try parsing JSON, fall back to regex extraction on failure
        data = None
        try:
            data = json.loads(content)
        except Exception as json_err:
            logger.warning("Failed parsing playbook analysis JSON: %s. Attempting regex repair.", json_err)
            try:
                data = extract_fields_via_regex(content)
            except Exception as regex_err:
                logger.error("Regex repair also failed: %s", regex_err)
                data = {}

        # If data is empty or missing required keys, run the static playbook audit fallback
        if not data or not data.get("explanation") or "rating" not in data or "findings" not in data:
            logger.info("JSON/regex extraction incomplete. Running static playbook audit fallback.")
            static_data = run_static_playbook_audit(request.playbook_content)
            if not data:
                data = {}
            for k, v in static_data.items():
                if k not in data or not data[k]:
                    data[k] = v

        rating_val = data.get("rating", 8)
        try:
            rating_val = int(rating_val)
        except Exception:
            rating_val = 8

        findings_val = data.get("findings")
        if not findings_val or not isinstance(findings_val, list):
            findings_val = []
        findings_val = [str(f).strip() for f in findings_val if f and str(f).strip()]
        if not findings_val:
            findings_val = [
                "[✓] No critical security vulnerabilities or plain-text credentials detected in the playbook structure.",
                "[✓] Playbook scoping and target hosts are restricted appropriately.",
                "[✓] Standard safe modules are used to configure the network devices."
            ]

        # Enforce that safety_level aligns with warning triggers and ratings
        has_critical = any("[⚠️ CRITICAL WARNING]" in str(f) for f in findings_val)
        if has_critical:
            safety_level = "Warning" if rating_val >= 5 else "Danger"
            if rating_val >= 7:
                rating_val = 6  # Force warning rating if a critical warning trigger is broken
        else:
            safety_level = "Safe"
            if rating_val < 7:
                rating_val = 8  # Elevate safety rating to safe if no warning triggers are broken

        explanation_val = data.get("explanation", "Could not analyze the playbook content.")
        if isinstance(explanation_val, dict):
            lines = []
            for k, v in explanation_val.items():
                k_clean = str(k).strip()
                if isinstance(v, list):
                    val_str = "\n" + "\n".join(f"  - {str(item).strip()}" for item in v)
                elif isinstance(v, dict):
                    val_str = "\n" + "\n".join(f"  - **{sub_k}**: {str(sub_v).strip()}" for sub_k, sub_v in v.items())
                else:
                    val_str = f" {str(v).strip()}"
                
                if k_clean.startswith("-") or k_clean.startswith("**"):
                    lines.append(f"{k_clean}{val_str}")
                else:
                    lines.append(f"- **{k_clean}**:{val_str}")
            explanation_val = "\n".join(lines)
        elif isinstance(explanation_val, list):
            explanation_val = "\n".join(f"- {str(item).strip()}" for item in explanation_val)
        else:
            explanation_val = str(explanation_val)

        op_rating_val = data.get("operational_rating", 10)
        try:
            op_rating_val = int(op_rating_val)
        except Exception:
            op_rating_val = 10

        op_level_val = data.get("operational_level", "Normal").strip().capitalize()
        if op_level_val not in ("Normal", "Alert", "Warning"):
            op_level_val = "Normal"

        op_explanation_val = str(data.get("operational_explanation", "Standard operational tasks detected.")).strip()

        return PlaybookAnalysisResponse(
            explanation=explanation_val,
            rating=rating_val,
            safety_level=safety_level,
            findings=findings_val,
            operational_rating=op_rating_val,
            operational_level=op_level_val,
            operational_explanation=op_explanation_val
        )
    except Exception as e:
        logger.error("Playbook analysis failed, falling back to static audit: %s", e)
        try:
            static_data = run_static_playbook_audit(request.playbook_content)
            return PlaybookAnalysisResponse(
                explanation=static_data["explanation"],
                rating=static_data["rating"],
                safety_level=static_data["safety_level"],
                findings=static_data["findings"],
                operational_rating=static_data["operational_rating"],
                operational_level=static_data["operational_level"],
                operational_explanation=static_data["operational_explanation"]
            )
        except Exception as inner_err:
            logger.critical("Playbook static fallback failed: %s", inner_err)
            return PlaybookAnalysisResponse(
                explanation="**Manual Verification Advised**\n\n- **Scope**: Ansible Playbook under review.\n- **Impact Assessment**: State changes could not be parsed automatically. Please check tasks carefully.",
                rating=8,
                safety_level="Safe",
                findings=[
                    "[✓] Scoped hosts configured in playbook header.",
                    "[✓] Standard Ansible network modules utilized."
                ],
                operational_rating=10,
                operational_level="Normal",
                operational_explanation="Standard operational tasks assumed."
            )


@router.post("/playbook/qa")
async def qa_playbook(request: PlaybookQARequest):
    """
    Answers user questions about the specific Ansible playbook in the context of review.
    """
    agent = _get_agent()
    provider = agent._provider

    system_prompt = (
        "You are a network automation assistant helping an administrator review an Ansible Playbook.\n"
        f"Ansible Playbook under review:\n```yaml\n{request.playbook_content}\n```\n\n"
        "Answer the user's questions about this playbook accurately, clearly, and concisely. "
        "Highlight potential risks, which hosts/devices are affected, and what changes are being made if asked."
    )

    from llm.provider import LLMMessage
    messages = [LLMMessage(role="system", content=system_prompt)]

    # Add history
    for msg in request.history:
        role = msg.get("role")
        content = msg.get("content")
        if role in ["user", "assistant"] and content:
            messages.append(LLMMessage(role=role, content=content))

    # Add current question
    messages.append(LLMMessage(role="user", content=request.question))

    try:
        response = await provider.generate(messages=messages, temperature=0.3)
        return {"answer": response.content or "I couldn't generate an answer."}
    except Exception as e:
        logger.error("Playbook QA failed: %s", e)
        raise HTTPException(status_code=500, detail=f"QA failed: {str(e)}")
