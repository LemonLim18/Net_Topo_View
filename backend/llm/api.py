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


def _provider_kind(provider: Any) -> str:
    provider_name = provider.__class__.__name__.lower() if provider is not None else ""
    if "ollama" in provider_name:
        return "ollama"
    return "gemini"


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
    browser_base_url = (ollama_base_url or active_base_url or os.getenv("OLLAMA_BASE_URL") or _DEFAULT_OLLAMA_VM_URL).rstrip("/")

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
            base_url=(ollama_base_url or os.getenv("OLLAMA_BASE_URL") or _DEFAULT_OLLAMA_VM_URL).rstrip("/"),
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
    # Build payload using the helper
    payload = build_controller_observability_payload(
        agent=agent,
        config_snapshots=_build_config_snapshots_payload(agent),
        deployment_state=_build_deployment_state_payload(agent),
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
        
        enrich_limit = 8 # Limit to avoid excessive API latency
        discovered_links: List[Dict[str, str]] = []
        seen_links = set()
        
        for i, d in enumerate(devices):
            # Detailed enrichment for smaller inventories
            interfaces_summary = ""
            links_summary = ""
            
            # Prioritize current prompt mention if possible, or just first few
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
                        # Just names and IPs to save tokens
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
                    pass # Skip enrichment on failure

            inventory_text += f"- {hostname} ({d.get('management_ip')}), {d.get('vendor')} {d.get('role')}{interfaces_summary}{links_summary}\n"
        
        if total_count > enrich_limit:
            inventory_text += f"\n(Note: Detailed enrichment skipped for {total_count - enrich_limit} devices.)\n"

        # Add explicit topology link facts (live + planned) so pairwise interface questions are answerable.
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
            "When asked about connections between two devices, prioritize LINK FACTS and quote both local and peer interfaces.\n\n"
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
    agent = _get_agent()
    proposals = agent.proposal_manager.list_all()
    return build_controller_observability_payload(
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


class PlaybookAnalysisResponse(BaseModel):
    explanation: str
    rating: int
    safety_level: str
    findings: List[str]


class PlaybookQARequest(BaseModel):
    playbook_content: str
    question: str
    history: List[Dict[str, str]] = Field(default_factory=list)


@router.post("/playbook/analyze", response_model=PlaybookAnalysisResponse)
async def analyze_playbook(request: PlaybookAnalysisRequest):
    """
    Analyzes an Ansible playbook, giving a plain English explanation,
    a safety rating (0-10), a safety level, and specific risk findings.
    """
    agent = _get_agent()
    provider = agent._provider

    system_prompt = (
        "You are an expert network security auditor. Analyze the following Ansible Playbook.\n"
        "Provide your response in raw JSON format with exactly the following keys:\n"
        "- 'explanation': A concise, plain-English summary of what the playbook does, suitable for a non-technical reviewer (as a string, bullet points allowed).\n"
        "- 'rating': An integer safety rating between 0 and 10, where 10 is completely safe and 0 is extremely dangerous/destructive.\n"
        "- 'safety_level': A string, either 'Safe', 'Warning', or 'Danger'.\n"
        "- 'findings': A list of strings detailing specific safety findings, observations, or warnings.\n"
        "Do not include any formatting, markdown markers, or backticks around the JSON. Output only the pure JSON text."
    )
    user_prompt = f"Ansible Playbook:\n```yaml\n{request.playbook_content}\n```"

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

        data = json.loads(content)
        
        rating_val = data.get("rating", 5)
        try:
            rating_val = int(rating_val)
        except Exception:
            rating_val = 5

        return PlaybookAnalysisResponse(
            explanation=data.get("explanation", "Could not analyze the playbook content."),
            rating=rating_val,
            safety_level=data.get("safety_level", "Warning"),
            findings=data.get("findings", ["Manual review required."])
        )
    except Exception as e:
        logger.error("Playbook analysis failed: %s", e)
        return PlaybookAnalysisResponse(
            explanation="AI analysis failed or returned invalid format. Please review the raw playbook manually.",
            rating=5,
            safety_level="Warning",
            findings=[f"Auditor analysis error: {str(e)}", "Please check the playbook content for potential destructive commands manually."]
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
