"""
M4 Deployment API — REST endpoints for the pipeline.

Exposes the 6-stage deployment process to the frontend and LLM agent.
"""

import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

try:
    from auth.auth import get_current_user
    from auth.rbac import require_role
except ImportError:
    async def get_current_user():  # type: ignore
        return {"username": "dev", "role": "admin"}
    def require_role(role: str):  # type: ignore
        return get_current_user

from db.manager import DatabaseManager
from deploy.pipeline import DeploymentPipeline
from deploy.models import JobStatus, PipelineStage

logger = logging.getLogger("deploy.api")

router = APIRouter(prefix="/deploy", tags=["Deployment (M4)"])

# -----------------------------------------------------------------------
# Dependencies
# -----------------------------------------------------------------------
_pipeline: Optional[DeploymentPipeline] = None

def get_pipeline():
    if not _pipeline:
        raise HTTPException(503, "Deployment pipeline not initialized")
    return _pipeline

def setup_pipeline(db_manager: DatabaseManager):
    """Initialize the global pipeline instance."""
    global _pipeline
    _pipeline = DeploymentPipeline(db_manager)
    logger.info("Deployment pipeline initialized")

# -----------------------------------------------------------------------
# Request Models
# -----------------------------------------------------------------------

class CreateRequest(BaseModel):
    title: str
    description: str = ""
    requester: str = "user"

class CreateJob(BaseModel):
    request_id: int
    device_id: int

class PlanJob(BaseModel):
    vendor: str
    feature: str
    params: Dict[str, Any]

class ExecuteJob(BaseModel):
    host: str
    username: str
    password: str
    driver_type: str = "cisco_ios"
    # Future: verification checks list

# -----------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------

@router.post("/requests", response_model=Dict[str, int])
async def create_request(
    req: CreateRequest,
    pipeline: DeploymentPipeline = Depends(get_pipeline),
    _user: dict = Depends(require_role("operator")),
):
    """Create a new logical Change Request."""
    req_id = pipeline.create_change_request(req.title, req.requester, req.description)
    return {"request_id": req_id}

@router.post("/jobs", response_model=Dict[str, int])
async def add_job(
    job: CreateJob,
    pipeline: DeploymentPipeline = Depends(get_pipeline),
    _user: dict = Depends(require_role("operator")),
):
    """Add a deployment job (target device) to a request."""
    job_id = pipeline.add_job(job.request_id, job.device_id)
    return {"job_id": job_id}

@router.get("/jobs", response_model=List[Dict])
async def list_jobs(
    pipeline: DeploymentPipeline = Depends(get_pipeline),
    _user: dict = Depends(get_current_user),
):
    """List all deployment jobs."""
    return pipeline.get_all_jobs()

@router.post("/jobs/{job_id}/plan")
async def plan_job(
    job_id: int,
    plan: PlanJob,
    pipeline: DeploymentPipeline = Depends(get_pipeline),
    _user: dict = Depends(require_role("operator")),
):
    """Run Stage 1: Generate configuration and rollback."""
    success = await run_in_threadpool(
        pipeline.run_plan_stage, job_id, plan.vendor, plan.feature, plan.params
    )
    if not success:
        raise HTTPException(500, "Plan generation failed (check logs)")
    return {"status": "success", "result": "Plan generated"}

@router.post("/jobs/{job_id}/execute")
async def execute_job(
    job_id: int,
    exec_params: ExecuteJob,
    pipeline: DeploymentPipeline = Depends(get_pipeline),
    _user: dict = Depends(require_role("admin")),
):
    """
    Run Stages 3-6: Snapshot -> Apply -> Verify -> Commit.

    WARNING: This applies changes to the live device!
    """
    success = await run_in_threadpool(
        pipeline.execute_job,
        job_id,
        host=exec_params.host,
        username=exec_params.username,
        password=exec_params.password,
        driver_type=exec_params.driver_type,
    )
    if not success:
        return {
            "status": "failed",
            "message": "Execution failed. Automatic rollback is disabled; review logs and approve rollback explicitly if needed.",
        }
    return {"status": "success", "message": "Deployment completed successfully"}

@router.get("/jobs/{job_id}/logs")
async def get_job_logs(
    job_id: int,
    pipeline: DeploymentPipeline = Depends(get_pipeline),
    _user: dict = Depends(get_current_user),
):
    """Get the step-by-step execution logs for a job."""
    logs = pipeline.get_job_logs(job_id)
    return {"logs": logs}


# ── Audit log endpoints ──────────────────────────────────────────────────

@router.get("/audit")
async def get_audit_log(
    limit: int = 50,
    _user: dict = Depends(get_current_user),
):
    """Return the most recent audit entries (newest first)."""
    if not _pipeline:
        raise HTTPException(503, "Pipeline not initialized")
    from deploy.audit import AuditLogger
    al = AuditLogger(_pipeline.db_manager)
    return {"entries": al.get_recent(limit=limit)}


@router.get("/audit/request/{request_id}")
async def get_audit_for_request(
    request_id: int,
    _user: dict = Depends(get_current_user),
):
    """Return audit entries for a specific change request."""
    if not _pipeline:
        raise HTTPException(503, "Pipeline not initialized")
    from deploy.audit import AuditLogger
    al = AuditLogger(_pipeline.db_manager)
    return {"entries": al.get_log_for_request(request_id)}


@router.get("/audit/job/{job_id}")
async def get_audit_for_job(
    job_id: int,
    _user: dict = Depends(get_current_user),
):
    """Return audit entries for a specific job."""
    if not _pipeline:
        raise HTTPException(503, "Pipeline not initialized")
    from deploy.audit import AuditLogger
    al = AuditLogger(_pipeline.db_manager)
    return {"entries": al.get_log_for_job(job_id)}
