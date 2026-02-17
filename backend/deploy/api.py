"""
M4 Deployment API — REST endpoints for the pipeline.

Exposes the 6-stage deployment process to the frontend and LLM agent.
"""

import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from backend.db.manager import DatabaseManager
from backend.deploy.pipeline import DeploymentPipeline
from backend.deploy.models import JobStatus, PipelineStage

logger = logging.getLogger("deploy.api")

router = APIRouter(prefix="/api/deploy", tags=["Deployment (M4)"])

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
    pipeline: DeploymentPipeline = Depends(get_pipeline)
):
    """Create a new logical Change Request."""
    req_id = pipeline.create_change_request(req.title, req.requester, req.description)
    return {"request_id": req_id}

@router.post("/jobs", response_model=Dict[str, int])
async def add_job(
    job: CreateJob, 
    pipeline: DeploymentPipeline = Depends(get_pipeline)
):
    """Add a deployment job (target device) to a request."""
    job_id = pipeline.add_job(job.request_id, job.device_id)
    return {"job_id": job_id}

@router.get("/jobs", response_model=List[Dict])
async def list_jobs(pipeline: DeploymentPipeline = Depends(get_pipeline)):
    """List all deployment jobs."""
    return pipeline.get_all_jobs()

@router.post("/jobs/{job_id}/plan")
async def plan_job(
    job_id: int, 
    plan: PlanJob,
    pipeline: DeploymentPipeline = Depends(get_pipeline)
):
    """Run Stage 1: Generate configuration and rollback."""
    success = pipeline.run_plan_stage(job_id, plan.vendor, plan.feature, plan.params)
    if not success:
        raise HTTPException(500, "Plan generation failed (check logs)")
    return {"status": "success", "result": "Plan generated"}

@router.post("/jobs/{job_id}/execute")
async def execute_job(
    job_id: int, 
    exec_params: ExecuteJob,
    pipeline: DeploymentPipeline = Depends(get_pipeline)
):
    """
    Run Stages 3-6: Snapshot -> Apply -> Verify -> Commit.
    
    WARNING: This applies changes to the live device!
    """
    success = pipeline.execute_job(
        job_id, 
        host=exec_params.host,
        username=exec_params.username,
        password=exec_params.password,
        driver_type=exec_params.driver_type
    )
    
    if not success:
        return {"status": "failed", "message": "Execution failed (rolled back if possible)"}
    
    return {"status": "success", "message": "Deployment completed successfully"}

@router.get("/jobs/{job_id}/logs")
async def get_job_logs(
    job_id: int,
    pipeline: DeploymentPipeline = Depends(get_pipeline)
):
    """Get the step-by-step execution logs for a job."""
    logs = pipeline.get_job_logs(job_id)
    return {"logs": logs}
