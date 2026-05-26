"""
M4 Deployment Pipeline - Approval Gate API.

Approval queue and request lifecycle endpoints.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel

from auth.rbac import require_admin, require_any
from db.manager import DatabaseManager
from deploy.audit import AuditLogger
from deploy.change_request import ChangeRequestService
from deploy.models import RequestStatus

logger = logging.getLogger("deploy.approval")
router = APIRouter(prefix="/api/deploy", tags=["deploy-approval"])


class ApprovalBody(BaseModel):
    comment: Optional[str] = None


_db_manager: Optional[DatabaseManager] = None


def set_db_manager(db: DatabaseManager) -> None:
    global _db_manager
    _db_manager = db
    try:
        # Ensure audit_log has modern columns before queue endpoints read review metadata.
        from deploy.audit import AuditLogger
        AuditLogger(db)
    except Exception as exc:
        logger.debug("Audit schema warm-up skipped: %s", exc)


def _get_db() -> DatabaseManager:
    if _db_manager is None:
        raise RuntimeError("DatabaseManager not initialised in approval module")
    return _db_manager


def _latest_audit(
    db: DatabaseManager,
    *,
    request_id: Optional[int] = None,
    job_id: Optional[int] = None,
) -> dict:
    clauses = []
    params = []
    if request_id is not None:
        clauses.append("request_id = ?")
        params.append(request_id)
    if job_id is not None:
        clauses.append("job_id = ?")
        params.append(job_id)
    if not clauses:
        return {}

    with db._connect() as conn:
        try:
            row = conn.execute(
                "SELECT action, actor, comment, created_at "
                f"FROM audit_log WHERE {' AND '.join(clauses)} AND action IN ('approve', 'reject') "
                "ORDER BY id DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        except Exception:
            # Legacy databases may not yet include actor/request_id/job_id columns.
            return {}
    return dict(row) if row else {}


def _queue_status_where(status_filter: str) -> tuple[str, str]:
    normalized = (status_filter or "pending").strip().lower()
    if normalized == "approved":
        return "status = 'approved'", "j.status = 'approved'"
    if normalized == "rejected":
        return "status = 'rejected'", "j.status = 'rejected'"
    return "status = 'pending_approval'", "j.stage = 'approve' AND j.status IN ('pending', 'pending_approval')"


def _change_request_hide_if_ansible_job_exists_sql(status_filter: str) -> str:
    """
    Ansible Studio creates both a change_request and a jobs row. Listing both produced
    duplicate queue lines; the job row carries ansible_entrypoint_path for preview/check.
    Hide the parent change_request when a matching job is already shown for the tab.
    """
    normalized = (status_filter or "pending").strip().lower()
    if normalized == "pending":
        sub = (
            "SELECT request_id FROM jobs WHERE request_id IS NOT NULL "
            "AND stage = 'approve' AND status IN ('pending', 'pending_approval')"
        )
    elif normalized == "approved":
        sub = "SELECT request_id FROM jobs WHERE request_id IS NOT NULL AND status = 'approved'"
    elif normalized == "rejected":
        sub = "SELECT request_id FROM jobs WHERE request_id IS NOT NULL AND status = 'rejected'"
    else:
        return ""
    return f" AND id NOT IN ({sub})"


def _serialize_queue_item(item_type: str, row, db: DatabaseManager) -> dict:
    request_id = row["request_id"] if "request_id" in row.keys() else row["id"]
    job_id = row["id"] if item_type == "ansible_job" else None
    review = _latest_audit(db, request_id=request_id, job_id=job_id)
    return {
        "id": row["id"],
        "type": item_type,
        "title": row["title"],
        "requester": row["requester"] or "system",
        "status": row["status"],
        "created_at": row["created_at"],
        "request_id": request_id,
        "entrypoint_path": row["ansible_entrypoint_path"] if "ansible_entrypoint_path" in row.keys() else None,
        "review_comment": review.get("comment"),
        "review_actor": review.get("actor"),
        "reviewed_at": review.get("created_at"),
        "owner_username": row["owner_username"] if "owner_username" in row.keys() else None,
    }


@router.post("/{request_id}/approve")
async def approve_request(
    request_id: int,
    body: ApprovalBody,
    db: DatabaseManager = Depends(_get_db),
    user: dict = Depends(require_admin),
):
    """Approve a change request that is in PENDING_APPROVAL state."""
    svc = ChangeRequestService(db)
    req = svc.get(request_id)
    if req is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"Request #{request_id} not found")

    if req.status != RequestStatus.PENDING_APPROVAL:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            f"Cannot approve - current status is '{req.status.value}', expected 'pending_approval'",
        )

    actor = user.get("username", "admin")
    ok = svc.approve(request_id, actor=actor)
    if not ok:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "Approval transition failed")

    AuditLogger(db).log(
        action="approve",
        request_id=request_id,
        actor=actor,
        comment=body.comment or "Approved via API",
    )
    logger.info("Request #%d approved by %s", request_id, actor)
    return {"status": "approved", "request_id": request_id}


@router.post("/{request_id}/reject")
async def reject_request(
    request_id: int,
    body: ApprovalBody,
    db: DatabaseManager = Depends(_get_db),
    user: dict = Depends(require_admin),
):
    """Reject a pending change request."""
    svc = ChangeRequestService(db)
    req = svc.get(request_id)
    if req is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"Request #{request_id} not found")

    if req.status != RequestStatus.PENDING_APPROVAL:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            f"Cannot reject - current status is '{req.status.value}'",
        )

    actor = user.get("username", "admin")
    ok = svc.reject(request_id, actor=actor)
    if not ok:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "Rejection transition failed")

    AuditLogger(db).log(
        action="reject",
        request_id=request_id,
        actor=actor,
        comment=body.comment or "Rejected via API",
    )
    logger.info("Request #%d rejected by %s", request_id, actor)
    return {"status": "rejected", "request_id": request_id}


@router.get("/{request_id}/status")
async def get_request_status(
    request_id: int,
    db: DatabaseManager = Depends(_get_db),
    user: dict = Depends(require_any),
):
    """Return the current status of a change request."""
    svc = ChangeRequestService(db)
    req = svc.get(request_id)
    if req is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"Request #{request_id} not found")
    if user.get("role") != "admin" and req.owner_username != user.get("username"):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "Access to this change request is restricted to its owner and administrators.")
    return {
        "request_id": request_id,
        "title": req.title,
        "status": req.status.value,
        "requester": req.requester,
    }


@router.get("/queue")
async def list_approval_queue(
    status_filter: str = "pending",
    limit: int = 50,
    db: DatabaseManager = Depends(_get_db),
    user: dict = Depends(require_admin),
):
    """Approval queue with Pending, Approved, and Rejected tabs."""
    request_where, job_where = _queue_status_where(status_filter)
    items = []

    is_admin = user.get("role") == "admin"
    owner_username = user.get("username")

    try:
        cr_extra = _change_request_hide_if_ansible_job_exists_sql(status_filter)
        cr_clause = "owner_username = ? OR owner_username IS NULL" if (owner_username and not is_admin) else "1=1"
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, requester, status, created_at, owner_username "
                f"FROM change_requests WHERE {request_where}{cr_extra} AND ({cr_clause}) "
                "ORDER BY created_at DESC LIMIT ?",
                (owner_username, limit) if (owner_username and not is_admin) else (limit,),
            ).fetchall()
        items.extend(_serialize_queue_item("change_request", row, db) for row in rows)
    except Exception as e:
        logger.debug("Could not fetch %s change_requests: %s", status_filter, e)

    try:
        job_clause = "j.owner_username = ? OR j.owner_username IS NULL" if (owner_username and not is_admin) else "1=1"
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT j.id, j.request_id, j.status, j.stage, j.created_at, j.ansible_entrypoint_path, j.owner_username, "
                "       COALESCE(cr.title, 'Ansible Job #' || j.id) AS title, "
                "       COALESCE(cr.requester, 'system') AS requester "
                "FROM jobs j "
                "LEFT JOIN change_requests cr ON j.request_id = cr.id "
                f"WHERE {job_where} AND ({job_clause}) "
                "ORDER BY j.created_at DESC LIMIT ?",
                (owner_username, limit) if (owner_username and not is_admin) else (limit,),
            ).fetchall()
        items.extend(_serialize_queue_item("ansible_job", row, db) for row in rows)
    except Exception as e:
        logger.debug("Could not fetch %s ansible jobs: %s", status_filter, e)

    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {
        "items": items[:limit],
        "total": len(items),
        "status_filter": status_filter,
    }


@router.get("/queue/pending")
async def list_pending_approvals(
    limit: int = 50,
    db: DatabaseManager = Depends(_get_db),
    user: dict = Depends(require_admin),
):
    """Backward-compatible alias for the Pending queue."""
    return await list_approval_queue("pending", limit, db, user)
