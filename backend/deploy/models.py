"""
M4 Deployment Pipeline — Data Models.

Defines the structure for Change Requests, Jobs, and Job Steps.
Used by the pipeline orchestrator and the API.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

from llm.context_contract import CONTEXT_DOMAIN_DEPLOYMENT, CONTEXT_DOMAIN_LIVE_TOPOLOGY
from llm.refresh_fabric import publish_context_refresh_event


class RequestStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    SKIPPED = "skipped"


class PipelineStage(str, Enum):
    PLAN = "plan"
    APPROVE = "approve"
    SNAPSHOT_PRE = "snapshot_pre"
    APPLY = "apply"
    VERIFY = "verify"
    COMMIT = "commit"
    ROLLBACK = "rollback"


class ExecutionEngine(str, Enum):
    """M22: Which execution path should this job use."""
    ANSIBLE_NATIVE = "ansible_native"   # Vendor resource modules (desired-state)
    ANSIBLE_CLI    = "ansible_cli"      # network_cli transport (IOS/NX-OS/VRP)
    SSH_FALLBACK   = "ssh_fallback"     # Netmiko/Paramiko fallback (hop-chains, exotic vendors)
    NO_OP          = "no_op"            # RAG confirmed already converged — nothing to push


class JobStep(BaseModel):
    """Detailed log of a single action within a job."""
    id: Optional[int] = None
    job_id: int
    stage: PipelineStage
    status: str = "running"
    details: str = ""
    created_at: Optional[datetime] = None


class Job(BaseModel):
    """A deployment task for a single device."""
    id: Optional[int] = None
    request_id: int
    device_id: int
    status: JobStatus = JobStatus.PENDING
    stage: PipelineStage = PipelineStage.PLAN
    snapshot_pre_id: Optional[int] = None
    snapshot_post_id: Optional[int] = None
    generated_config: Optional[str] = None
    rollback_config: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    # M22 — Ansible-first execution
    execution_engine: Optional[ExecutionEngine] = None  # None → SSH_FALLBACK at runtime
    ansible_playbook: Optional[str] = None              # LLM-generated YAML stored here
    ansible_artifact_manifest: Optional[str] = None     # JSON manifest for generated Ansible bundles
    ansible_entrypoint_path: Optional[str] = None       # Relative file path of generated entry playbook

    # Joined fields (for API response)
    hostname: Optional[str] = None
    steps: List[JobStep] = []


class ChangeRequest(BaseModel):
    """A logical group of configuration changes."""
    id: Optional[int] = None
    title: str
    description: Optional[str] = None
    status: RequestStatus = RequestStatus.DRAFT
    requester: str = "unknown"
    created_at: Optional[datetime] = None
    
    # Joined fields
    jobs: List[Job] = []


# ---------------------------------------------------------------------------
# Database Helpers (M14 Integration)
# ---------------------------------------------------------------------------

class DeploymentRepository:
    """
    Data access layer for M4 tables. 
    Ideally this would be part of M14, but we keep it here for modularity
    until M14 exposes a plugin system.
    """
    
    def __init__(self, db_manager):
        self.db = db_manager

    def create_request(self, req: ChangeRequest) -> int:
        """Create a new Change Request and return its ID."""
        with self.db._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO change_requests (title, description, status, requester)
                VALUES (?, ?, ?, ?)
                """,
                (req.title, req.description, req.status.value, req.requester)
            )
            request_id = cur.lastrowid
        publish_context_refresh_event(
            source="deployment_repository",
            action="change_request_created",
            summary=f"change request created: {request_id}",
            domains=(CONTEXT_DOMAIN_DEPLOYMENT,),
            event_type="refresh",
            refresh_mode="none",
            metadata={"request_id": request_id, "status": req.status.value, "title": req.title},
        )
        return request_id

    def get_request(self, request_id: int) -> Optional[ChangeRequest]:
        """Get a Change Request by ID (without joining jobs)."""
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM change_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if not row:
                return None
            return ChangeRequest(
                id=row["id"],
                title=row["title"],
                description=row["description"],
                status=RequestStatus(row["status"]),
                requester=row["requester"],
                created_at=row["created_at"],
            )

    def create_job(self, job: Job) -> int:
        """Create a new Job for a Change Request."""
        with self.db._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO jobs (request_id, device_id, status, stage)
                VALUES (?, ?, ?, ?)
                """,
                (job.request_id, job.device_id, job.status.value, job.stage.value)
            )
            job_id = cur.lastrowid
        publish_context_refresh_event(
            source="deployment_repository",
            action="deployment_job_created",
            summary=f"deployment job created: {job_id}",
            domains=(CONTEXT_DOMAIN_DEPLOYMENT,),
            event_type="refresh",
            refresh_mode="none",
            metadata={
                "job_id": job_id,
                "request_id": job.request_id,
                "device_id": job.device_id,
                "status": job.status.value,
                "stage": job.stage.value,
            },
        )
        return job_id

    def log_step(self, job_id: int, stage: PipelineStage, details: str) -> int:
        """Log a new step for a job."""
        with self.db._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO job_steps (job_id, stage, status, details)
                VALUES (?, ?, 'running', ?)
                """,
                (job_id, stage.value, details)
            )
            return cur.lastrowid

    def update_step_status(self, step_id: int, status: str, details: str = None):
        """Update the status of a job step."""
        with self.db._connect() as conn:
            if details:
                conn.execute(
                    "UPDATE job_steps SET status = ?, details = ? WHERE id = ?",
                    (status, details, step_id)
                )
            else:
                conn.execute(
                    "UPDATE job_steps SET status = ? WHERE id = ?",
                    (status, step_id)
                )

    def update_job_status(self, job_id: int, status: JobStatus, stage: PipelineStage, error: str = None):
        """Update job status/stage."""
        device_id = None
        hostname = None
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT device_id FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row:
                device_id = row["device_id"]
        with self.db._connect() as conn:
            conn.execute(
                """
                UPDATE jobs 
                SET status = ?, stage = ?, error_message = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (status.value, stage.value, error, job_id)
            )
        if device_id is not None:
            device = self.db.get_device_by_id(device_id)
            hostname = device.hostname if device else None

        domains = [CONTEXT_DOMAIN_DEPLOYMENT]
        refresh_targets = ()
        if stage in (PipelineStage.APPLY, PipelineStage.COMMIT, PipelineStage.ROLLBACK):
            domains.append(CONTEXT_DOMAIN_LIVE_TOPOLOGY)
        if stage in (PipelineStage.COMMIT, PipelineStage.ROLLBACK):
            refresh_targets = ("live_topology",)

        publish_context_refresh_event(
            source="deployment_pipeline",
            action="deployment_job_status_updated",
            summary=f"deployment {stage.value} for job {job_id}",
            domains=tuple(domains),
            event_type="invalidate",
            refresh_targets=refresh_targets,
            metadata={
                "job_id": job_id,
                "device_id": device_id,
                "hostname": hostname,
                "status": status.value,
                "stage": stage.value,
                "error": error,
            },
        )
            
    def list_jobs(self) -> List[Dict]:
        """Return all jobs joined with request title and device hostname."""
        with self.db._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.id, j.request_id, j.device_id, j.status, j.stage,
                       j.error_message, j.execution_engine,
                       j.created_at, j.updated_at,
                       cr.title AS request_title,
                       d.hostname AS hostname
                FROM jobs j
                LEFT JOIN change_requests cr ON cr.id = j.request_id
                LEFT JOIN devices d ON d.id = j.device_id
                ORDER BY j.id DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def update_request_status(self, req_id: int, status: RequestStatus):
        """Update overall request status."""
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE change_requests SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status.value, req_id)
            )
        publish_context_refresh_event(
            source="deployment_repository",
            action="change_request_status_updated",
            summary=f"change request status updated: {req_id} -> {status.value}",
            domains=(CONTEXT_DOMAIN_DEPLOYMENT,),
            event_type="refresh",
            refresh_mode="none",
            metadata={"request_id": req_id, "status": status.value},
        )
