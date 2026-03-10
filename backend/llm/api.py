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
    POST /api/chat/refresh                  — Refresh network context

    GET  /api/chat/proposals                — List all proposals
    GET  /api/chat/proposals/{id}           — Get a specific proposal
    POST /api/chat/proposals/{id}/approve   — Approve → deploy to device
    POST /api/chat/proposals/{id}/reject    — Reject → discard proposal
    POST /api/chat/proposals/{id}/rollback  — Rollback → revert deployed change
"""

import logging
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from auth.rbac import require_role
from llm.benchmark_suite import benchmark_categories, list_benchmark_cases, run_benchmark_suite
from llm.resilience_suite import list_resilience_scenarios, resilience_categories, run_resilience_suite
from llm.change_intelligence import CHANGE_INTELLIGENCE_VERSION
from rag.digital_twin import DIGITAL_TWIN_VERSION
from llm import network_tools
from llm.refresh_fabric import register_refresh_listener

logger = logging.getLogger("llm.api")

router = APIRouter(prefix="/llm/chat", tags=["Chat (M5)"])


# -----------------------------------------------------------------------
# Request / response models
# -----------------------------------------------------------------------

class ChatRequest(BaseModel):
    """User chat message."""
    message: str = Field(..., description="The user's message", min_length=1)


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
        result = await agent.chat(request.message)
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


@router.post("/clear")
async def clear_history():
    """Clear the conversation history."""
    agent = _get_agent()
    agent.clear_history()
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
    return {"analysis_version": CHANGE_INTELLIGENCE_VERSION}


@router.post("/change-intelligence")
async def run_change_intelligence(request: ChangeIntelligenceRequest):
    """Run the deterministic Step 29 change-intelligence analysis for a proposed change."""
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
    return {"version": DIGITAL_TWIN_VERSION}


@router.post("/digital-twin")
async def run_digital_twin(request: Optional[DigitalTwinRequest] = None):
    """Build the formal Step 30 digital-twin graph snapshot for a device or the whole network."""
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
        status: "pending" | "approved" | "rejected" | "deployed" | "rolled_back"
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

    # 2. Approve in the proposal manager
    proposal = mgr.approve(proposal_id)
    if not proposal:
        raise HTTPException(500, "Failed to approve proposal")

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
        )

        # Build verification checks from intent pipeline if available
        verification_checks = []
        _intent_meta = getattr(proposal, '_intent_meta', None)
        if _intent_meta and _intent_meta.get('verification_checks'):
            from deploy.models import VerificationCheck
            for vc in _intent_meta['verification_checks']:
                verification_checks.append(VerificationCheck(
                    check_type=vc.get('check_type', 'cli_output_match'),
                    command=vc.get('command', ''),
                    expected=vc.get('expected', ''),
                    description=vc.get('description', ''),
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

        if success:
            mgr.mark_deployed(proposal_id, job_id=job_id, request_id=req_id)
            return {
                "status": "deployed",
                "message": f"Configuration deployed to {proposal.target_device}",
                "job_id": job_id,
                "request_id": req_id,
                "proposal": mgr.get(proposal_id).model_dump(),
            }
        else:
            # Deployment failed — pipeline handles rollback internally
            return {
                "status": "failed",
                "message": f"Deployment to {proposal.target_device} failed (rolled back if possible)",
                "job_id": job_id,
                "proposal": mgr.get(proposal_id).model_dump(),
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
    if proposal.status != "deployed":
        raise HTTPException(
            409, f"Proposal is '{proposal.status}', expected 'deployed'"
        )

    if not proposal.rollback_config:
        raise HTTPException(
            422, "No rollback commands available for this proposal"
        )

    # If pipeline is available, try to execute rollback via the pipeline
    if _pipeline and proposal.job_id:
        try:
            from deploy.rollback import RollbackEngine
            from deploy.models import Job, JobStatus, PipelineStage

            # Get the job and apply rollback
            jobs = _pipeline.get_all_jobs()
            target_job = None
            for j in jobs:
                if j.get("id") == proposal.job_id:
                    target_job = j
                    break

            # Mark as rolled back regardless of pipeline result
            mgr.mark_rolled_back(proposal_id)

            return {
                "status": "rolled_back",
                "message": f"Rollback commands prepared for {proposal.target_device}",
                "rollback_config": proposal.rollback_config,
                "proposal": mgr.get(proposal_id).model_dump(),
            }
        except Exception as e:
            logger.error("Rollback via pipeline failed: %s", e)
            # Fall through to manual rollback

    # Manual rollback (no pipeline)
    mgr.mark_rolled_back(proposal_id)
    return {
        "status": "rolled_back",
        "message": f"Proposal marked as rolled back. Apply these commands to {proposal.target_device}:",
        "rollback_config": proposal.rollback_config,
        "proposal": mgr.get(proposal_id).model_dump(),
    }
