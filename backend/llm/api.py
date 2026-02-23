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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
    type: str = "text"                     # "text" | "proposal"
    proposal: Optional[Dict[str, Any]] = None  # ConfigProposal dict


class InfoResponse(BaseModel):
    """Agent info."""
    model: str = ""
    tools: list = []
    history_length: int = 0
    status: str = "ready"


# -----------------------------------------------------------------------
# Shared agent instance (set by setup_agent)
# -----------------------------------------------------------------------
_agent = None
_pipeline = None  # DeploymentPipeline (M4)
_vault = None     # CredentialVault (M15)


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
    )


@router.post("/refresh")
async def refresh_context():
    """
    Refresh the agent's network context.
    Call this after a topology crawl completes.
    """
    agent = _get_agent()
    agent.refresh_context()
    return {"status": "ok", "message": "Network context refreshed"}


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


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str):
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
        _pipeline.run_plan_stage(
            job_id,
            vendor=proposal.vendor,
            feature=proposal.feature,
            params=proposal.params,
        )

        # Execute stages 3-6 (Snapshot → Apply → Verify → Commit)
        success = _pipeline.execute_job(
            job_id,
            host=ssh_host,
            username=creds["username"],
            password=creds["password"],
            driver_type=proposal.vendor,
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
async def reject_proposal(proposal_id: str):
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
async def rollback_proposal(proposal_id: str):
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
