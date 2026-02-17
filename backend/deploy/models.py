"""
M4 Deployment Pipeline — Data Models.

Defines the structure for Change Requests, Jobs, and Job Steps.
Used by the pipeline orchestrator and the API.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


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
            return cur.lastrowid

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
            return cur.lastrowid

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
        with self.db._connect() as conn:
            conn.execute(
                """
                UPDATE jobs 
                SET status = ?, stage = ?, error_message = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (status.value, stage.value, error, job_id)
            )
            
    def update_request_status(self, req_id: int, status: RequestStatus):
        """Update overall request status."""
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE change_requests SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status.value, req_id)
            )
