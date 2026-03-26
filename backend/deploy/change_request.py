"""
M4 Deployment Pipeline — Change Request CRUD helpers.

Thin service layer on top of DeploymentRepository for creating and
transitioning ChangeRequest lifecycle states.  The pipeline orchestrator
calls these helpers; the approval API (approval.py) also uses them.
"""

import logging
from typing import List, Optional

from db.manager import DatabaseManager
from deploy.models import ChangeRequest, RequestStatus, DeploymentRepository

logger = logging.getLogger("deploy.change_request")


class ChangeRequestService:
    """
    High-level CRUD + state-machine service for ChangeRequest objects.

    State machine:
        DRAFT → PENDING_APPROVAL → APPROVED → RUNNING → COMPLETED | FAILED | ROLLED_BACK
    """

    VALID_TRANSITIONS: dict[RequestStatus, list[RequestStatus]] = {
        RequestStatus.DRAFT:            [RequestStatus.PENDING_APPROVAL],
        RequestStatus.PENDING_APPROVAL: [RequestStatus.APPROVED, RequestStatus.REJECTED, RequestStatus.DRAFT],
        RequestStatus.APPROVED:         [RequestStatus.RUNNING, RequestStatus.REJECTED, RequestStatus.DRAFT],
        RequestStatus.REJECTED:         [],
        RequestStatus.RUNNING:          [RequestStatus.COMPLETED, RequestStatus.FAILED, RequestStatus.ROLLED_BACK],
        RequestStatus.COMPLETED:        [],
        RequestStatus.FAILED:           [RequestStatus.DRAFT],
        RequestStatus.ROLLED_BACK:      [RequestStatus.DRAFT],
    }

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.repo = DeploymentRepository(db_manager)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        title: str,
        requester: str,
        description: str = "",
    ) -> ChangeRequest:
        """Create a new change request in DRAFT state and return it."""
        req = ChangeRequest(title=title, description=description, requester=requester)
        req_id = self.repo.create_request(req)
        req.id = req_id
        logger.info("Created ChangeRequest #%d '%s' by %s", req_id, title, requester)
        return req

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, request_id: int) -> Optional[ChangeRequest]:
        return self.repo.get_request(request_id)

    def list_all(self, limit: int = 50) -> List[ChangeRequest]:
        """Return the most recent *limit* change requests."""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM change_requests ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            ChangeRequest(
                id=r["id"],
                title=r["title"],
                description=r["description"],
                status=RequestStatus(r["status"]),
                requester=r["requester"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def list_by_status(self, status: RequestStatus) -> List[ChangeRequest]:
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM change_requests WHERE status = ? ORDER BY id DESC",
                (status.value,),
            ).fetchall()
        return [
            ChangeRequest(
                id=r["id"],
                title=r["title"],
                description=r["description"],
                status=RequestStatus(r["status"]),
                requester=r["requester"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        request_id: int,
        new_status: RequestStatus,
        actor: str = "system",
    ) -> bool:
        """
        Move a request to *new_status* if the transition is valid.
        Returns True on success, False if transition is not allowed.
        """
        req = self.get(request_id)
        if req is None:
            logger.error("ChangeRequest #%d not found", request_id)
            return False

        allowed = self.VALID_TRANSITIONS.get(req.status, [])
        if new_status not in allowed:
            logger.warning(
                "Invalid transition %s → %s for request #%d",
                req.status, new_status, request_id,
            )
            return False

        self.repo.update_request_status(request_id, new_status)
        logger.info(
            "ChangeRequest #%d: %s → %s (by %s)",
            request_id, req.status.value, new_status.value, actor,
        )
        return True

    def submit_for_approval(self, request_id: int, actor: str = "system") -> bool:
        return self.transition(request_id, RequestStatus.PENDING_APPROVAL, actor)

    def approve(self, request_id: int, actor: str = "system") -> bool:
        return self.transition(request_id, RequestStatus.APPROVED, actor)

    def reject(self, request_id: int, actor: str = "system") -> bool:
        return self.transition(request_id, RequestStatus.REJECTED, actor)

    def mark_running(self, request_id: int) -> bool:
        return self.transition(request_id, RequestStatus.RUNNING, "pipeline")

    def mark_complete(self, request_id: int) -> bool:
        return self.transition(request_id, RequestStatus.COMPLETED, "pipeline")

    def mark_failed(self, request_id: int) -> bool:
        return self.transition(request_id, RequestStatus.FAILED, "pipeline")

    def mark_rolled_back(self, request_id: int) -> bool:
        return self.transition(request_id, RequestStatus.ROLLED_BACK, "pipeline")
