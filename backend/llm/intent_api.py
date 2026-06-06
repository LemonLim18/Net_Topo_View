"""
Phase 1 — Intent Lifecycle Management API (UC02-UC05).

Provides full CRUD for intents so operators can view history,
edit drafts, cancel, duplicate, and inspect linked playbooks/proposals.

Endpoints:
    GET    /intents             — list with filters + pagination
    GET    /intents/{id}        — detail + linked proposals/plans
    PUT    /intents/{id}        — edit (draft/parsed only)
    POST   /intents/{id}/cancel — cancel an intent
    POST   /intents/{id}/duplicate — clone with new UUID
    GET    /intents/{id}/playbooks — linked proposals & agent plans
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("api.intents")

router = APIRouter(prefix="/intents", tags=["Intent Lifecycle"])

# ---------------------------------------------------------------------------
# Module-level DB reference (injected at startup via setup_intents())
# ---------------------------------------------------------------------------
_db = None


def setup_intents(db) -> None:
    """Inject shared DatabaseManager — called once from ``backend.py`` lifespan."""
    global _db
    _db = db
    logger.info("Intent Lifecycle API initialized")


def _get_db():
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    return _db


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class IntentSummary(BaseModel):
    id: str
    user_prompt: str
    intent_type: str
    action: Optional[str] = None
    feature: Optional[str] = None
    vendor: Optional[str] = None
    risk_level: str = "medium"
    status: str = "parsed"
    blast_radius: int = 0
    resolved_devices: Optional[List[str]] = None
    policy_approved: Optional[bool] = None
    created_at: Optional[str] = None


class IntentDetail(IntentSummary):
    parameters: Optional[Dict[str, Any]] = None
    scope: Optional[Dict[str, Any]] = None
    policy_result: Optional[Dict[str, Any]] = None
    proposal_id: Optional[str] = None
    linked_proposals: List[Dict[str, Any]] = Field(default_factory=list)
    linked_plans: List[Dict[str, Any]] = Field(default_factory=list)


class IntentUpdate(BaseModel):
    user_prompt: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    scope: Optional[Dict[str, Any]] = None
    risk_level: Optional[str] = None
    vendor: Optional[str] = None
    feature: Optional[str] = None


class IntentListResponse(BaseModel):
    items: List[IntentSummary]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_field(val: Any) -> Any:
    """Safely parse a JSON string field from SQLite."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def _row_to_summary(row: dict) -> IntentSummary:
    devices = _parse_json_field(row.get("resolved_devices"))
    if isinstance(devices, str):
        devices = [devices]
    return IntentSummary(
        id=row["id"],
        user_prompt=row.get("user_prompt", ""),
        intent_type=row.get("intent_type", "query"),
        action=row.get("action"),
        feature=row.get("feature"),
        vendor=row.get("vendor"),
        risk_level=row.get("risk_level", "medium"),
        status=row.get("status", "parsed"),
        blast_radius=row.get("blast_radius", 0),
        resolved_devices=devices if isinstance(devices, list) else None,
        policy_approved=bool(row["policy_approved"]) if row.get("policy_approved") is not None else None,
        created_at=row.get("created_at"),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=IntentListResponse)
async def list_intents(
    status: Optional[str] = Query(None, description="Filter by status"),
    intent_type: Optional[str] = Query(None, description="Filter by intent_type"),
    risk_level: Optional[str] = Query(None, description="Filter by risk_level"),
    search: Optional[str] = Query(None, description="Search user_prompt text"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """List intents with filters and pagination (UC02)."""
    db = _get_db()
    try:
        with db._connect() as conn:
            where_clauses: List[str] = []
            params: List[Any] = []

            if status:
                where_clauses.append("status = ?")
                params.append(status)
            if intent_type:
                where_clauses.append("intent_type = ?")
                params.append(intent_type)
            if risk_level:
                where_clauses.append("risk_level = ?")
                params.append(risk_level)
            if search:
                where_clauses.append("user_prompt LIKE ?")
                params.append(f"%{search}%")

            where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            # Count
            count_row = conn.execute(
                f"SELECT COUNT(*) as n FROM intents{where_sql}", params
            ).fetchone()
            total = count_row["n"] if count_row else 0

            # Fetch page
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"SELECT * FROM intents{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()

            items = [_row_to_summary(dict(r)) for r in rows]

        return IntentListResponse(items=items, total=total, page=page, page_size=page_size)

    except Exception as e:
        logger.error("list_intents failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{intent_id}", response_model=IntentDetail)
async def get_intent(intent_id: str):
    """Get full intent detail with linked proposals and plans (UC02)."""
    db = _get_db()
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Intent '{intent_id}' not found")
            row = dict(row)

            # Linked config proposals
            proposals = conn.execute(
                "SELECT id, status, target_device, vendor, feature, risk_level, "
                "ai_explanation, created_at FROM config_proposals WHERE id = ?",
                (row.get("proposal_id") or "",),
            ).fetchall()

            # Also search by user_prompt match (intents may link to proposals via prompt)
            prompt_proposals = conn.execute(
                "SELECT id, status, target_device, vendor, feature, risk_level, "
                "ai_explanation, created_at FROM config_proposals "
                "WHERE user_prompt = ? AND id != ?",
                (row.get("user_prompt", ""), row.get("proposal_id") or ""),
            ).fetchall()

            # Linked agent plans
            plans = conn.execute(
                "SELECT id, domain, message, risk_level, status, created_at "
                "FROM agent_plans WHERE message = ?",
                (row.get("user_prompt", ""),),
            ).fetchall()

            devices = _parse_json_field(row.get("resolved_devices"))
            if isinstance(devices, str):
                devices = [devices]

            return IntentDetail(
                id=row["id"],
                user_prompt=row.get("user_prompt", ""),
                intent_type=row.get("intent_type", "query"),
                action=row.get("action"),
                feature=row.get("feature"),
                vendor=row.get("vendor"),
                risk_level=row.get("risk_level", "medium"),
                status=row.get("status", "parsed"),
                blast_radius=row.get("blast_radius", 0),
                resolved_devices=devices if isinstance(devices, list) else None,
                policy_approved=bool(row["policy_approved"]) if row.get("policy_approved") is not None else None,
                created_at=row.get("created_at"),
                parameters=_parse_json_field(row.get("parameters")),
                scope=_parse_json_field(row.get("scope")),
                policy_result=_parse_json_field(row.get("policy_result")),
                proposal_id=row.get("proposal_id"),
                linked_proposals=[dict(p) for p in proposals] + [dict(p) for p in prompt_proposals],
                linked_plans=[dict(p) for p in plans],
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_intent failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{intent_id}", response_model=IntentSummary)
async def update_intent(intent_id: str, body: IntentUpdate):
    """Edit an intent — only allowed when status is 'parsed' or 'draft' (UC03)."""
    db = _get_db()
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Intent '{intent_id}' not found")

            current_status = row["status"]
            if current_status not in ("parsed", "draft", "validated"):
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot edit intent in '{current_status}' status. "
                           f"Only 'parsed', 'draft', or 'validated' intents can be edited.",
                )

            # Build SET clause from provided fields
            updates: Dict[str, Any] = {}
            if body.user_prompt is not None:
                updates["user_prompt"] = body.user_prompt
            if body.parameters is not None:
                updates["parameters"] = json.dumps(body.parameters)
            if body.scope is not None:
                updates["scope"] = json.dumps(body.scope)
            if body.risk_level is not None:
                updates["risk_level"] = body.risk_level
            if body.vendor is not None:
                updates["vendor"] = body.vendor
            if body.feature is not None:
                updates["feature"] = body.feature

            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update")

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [intent_id]
            conn.execute(f"UPDATE intents SET {set_clause} WHERE id = ?", values)
            conn.commit()

            # Audit trail
            try:
                conn.execute(
                    "INSERT INTO audit_log (user_id, action, target, details) VALUES (?, ?, ?, ?)",
                    (None, "intent_edit", intent_id, json.dumps({"updated_fields": list(updates.keys())})),
                )
                conn.commit()
            except Exception:
                pass  # Non-critical

            # Return updated row
            updated = conn.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
            return _row_to_summary(dict(updated))

    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_intent failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{intent_id}/cancel", response_model=IntentSummary)
async def cancel_intent(intent_id: str):
    """Cancel an intent — sets status to 'cancelled' (UC04)."""
    db = _get_db()
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Intent '{intent_id}' not found")

            current_status = row["status"]
            terminal_states = ("executed", "verified", "cancelled")
            if current_status in terminal_states:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot cancel intent in '{current_status}' status.",
                )

            conn.execute(
                "UPDATE intents SET status = 'cancelled' WHERE id = ?",
                (intent_id,),
            )
            conn.commit()

            # Audit trail
            try:
                conn.execute(
                    "INSERT INTO audit_log (user_id, action, target, details) VALUES (?, ?, ?, ?)",
                    (None, "intent_cancel", intent_id, json.dumps({"previous_status": current_status})),
                )
                conn.commit()
            except Exception:
                pass

            updated = conn.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
            return _row_to_summary(dict(updated))

    except HTTPException:
        raise
    except Exception as e:
        logger.error("cancel_intent failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{intent_id}/duplicate", response_model=IntentSummary)
async def duplicate_intent(intent_id: str):
    """Clone an intent with a new UUID, resetting status to 'draft' (UC05)."""
    db = _get_db()
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Intent '{intent_id}' not found")
            row = dict(row)

            new_id = str(uuid.uuid4())[:8]
            now = datetime.utcnow().isoformat()

            conn.execute(
                """INSERT INTO intents
                   (id, user_prompt, intent_type, action, feature, vendor,
                    parameters, scope, risk_level, resolved_devices,
                    policy_approved, blast_radius, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
                (
                    new_id,
                    row.get("user_prompt", ""),
                    row.get("intent_type", "query"),
                    row.get("action"),
                    row.get("feature"),
                    row.get("vendor"),
                    row.get("parameters"),
                    row.get("scope"),
                    row.get("risk_level", "medium"),
                    row.get("resolved_devices"),
                    None,  # policy not yet evaluated
                    row.get("blast_radius", 0),
                    now,
                ),
            )
            conn.commit()

            # Audit trail
            try:
                conn.execute(
                    "INSERT INTO audit_log (user_id, action, target, details) VALUES (?, ?, ?, ?)",
                    (None, "intent_duplicate", new_id, json.dumps({"source_intent": intent_id})),
                )
                conn.commit()
            except Exception:
                pass

            new_row = conn.execute("SELECT * FROM intents WHERE id = ?", (new_id,)).fetchone()
            return _row_to_summary(dict(new_row))

    except HTTPException:
        raise
    except Exception as e:
        logger.error("duplicate_intent failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{intent_id}/playbooks")
async def get_intent_playbooks(intent_id: str):
    """Get all proposals and agent plans linked to an intent (UC02 detail)."""
    db = _get_db()
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Intent '{intent_id}' not found")
            row = dict(row)

            # Direct proposal link
            proposals = []
            if row.get("proposal_id"):
                direct = conn.execute(
                    "SELECT * FROM config_proposals WHERE id = ?",
                    (row["proposal_id"],),
                ).fetchall()
                proposals.extend([dict(p) for p in direct])

            # Match by user_prompt
            prompt_matches = conn.execute(
                "SELECT * FROM config_proposals WHERE user_prompt = ?",
                (row.get("user_prompt", ""),),
            ).fetchall()
            seen_ids = {p["id"] for p in proposals}
            for p in prompt_matches:
                if p["id"] not in seen_ids:
                    proposals.append(dict(p))

            # Agent plans matched by message
            plans = conn.execute(
                "SELECT * FROM agent_plans WHERE message = ?",
                (row.get("user_prompt", ""),),
            ).fetchall()

            return {
                "intent_id": intent_id,
                "proposals": proposals,
                "agent_plans": [dict(p) for p in plans],
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_intent_playbooks failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
