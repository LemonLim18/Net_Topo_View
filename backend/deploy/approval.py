"""
M4 Deployment Pipeline — Approval Gate API.

REST endpoints for human approval / rejection of change requests before
the pipeline is allowed to push config.  Designed to be mounted into the
main FastAPI app via deploy/api.py.

Routes exposed:
  POST /api/deploy/{request_id}/approve  → approve the request
  POST /api/deploy/{request_id}/reject   → reject / send back to DRAFT
  GET  /api/deploy/{request_id}/status   → current status (lightweight)
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel
from typing import Optional

from db.manager import DatabaseManager
from deploy.models import RequestStatus
from deploy.change_request import ChangeRequestService
from deploy.audit import AuditLogger

logger = logging.getLogger("deploy.approval")
router = APIRouter(prefix="/api/deploy", tags=["deploy-approval"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class ApprovalBody(BaseModel):
    actor: str = "admin"
    comment: Optional[str] = None


# ---------------------------------------------------------------------------
# Dependency — reuse app-level db_manager injected by backend.py
# ---------------------------------------------------------------------------

_db_manager: Optional[DatabaseManager] = None


def set_db_manager(db: DatabaseManager) -> None:
    global _db_manager
    _db_manager = db


def _get_db() -> DatabaseManager:
    if _db_manager is None:
        raise RuntimeError("DatabaseManager not initialised in approval module")
    return _db_manager


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/{request_id}/approve")
async def approve_request(
    request_id: int,
    body: ApprovalBody,
    db: DatabaseManager = Depends(_get_db),
):
    """
    Approve a change request that is in PENDING_APPROVAL state.
    Transitions it to APPROVED so the pipeline can proceed.
    """
    svc = ChangeRequestService(db)
    req = svc.get(request_id)
    if req is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"Request #{request_id} not found")

    if req.status != RequestStatus.PENDING_APPROVAL:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            f"Cannot approve — current status is '{req.status.value}', expected 'pending_approval'",
        )

    ok = svc.approve(request_id, actor=body.actor)
    if not ok:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "Approval transition failed")

    AuditLogger(db).log(
        action="approve",
        request_id=request_id,
        actor=body.actor,
        comment=body.comment or "Approved via API",
    )
    logger.info("Request #%d approved by %s", request_id, body.actor)
    return {"status": "approved", "request_id": request_id}


@router.post("/{request_id}/reject")
async def reject_request(
    request_id: int,
    body: ApprovalBody,
    db: DatabaseManager = Depends(_get_db),
):
    """
    Reject a pending change request — moves it back to DRAFT.
    """
    svc = ChangeRequestService(db)
    req = svc.get(request_id)
    if req is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"Request #{request_id} not found")

    if req.status != RequestStatus.PENDING_APPROVAL:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            f"Cannot reject — current status is '{req.status.value}'",
        )

    ok = svc.reject(request_id, actor=body.actor)
    if not ok:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "Rejection transition failed")

    AuditLogger(db).log(
        action="reject",
        request_id=request_id,
        actor=body.actor,
        comment=body.comment or "Rejected via API",
    )
    logger.info("Request #%d rejected by %s", request_id, body.actor)
    return {"status": "rejected", "request_id": request_id}


@router.get("/{request_id}/status")
async def get_request_status(
    request_id: int,
    db: DatabaseManager = Depends(_get_db),
):
    """Return the current status of a change request."""
    svc = ChangeRequestService(db)
    req = svc.get(request_id)
    if req is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"Request #{request_id} not found")
    return {
        "request_id": request_id,
        "title": req.title,
        "status": req.status.value,
        "requester": req.requester,
    }
