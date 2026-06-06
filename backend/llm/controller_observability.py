from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional


CONTROLLER_OBSERVABILITY_VERSION = "phase4_step14_v1"

_LATEST_CHAT_SNAPSHOT: Optional[Dict[str, Any]] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _proposal_dict(proposal: Any) -> Dict[str, Any]:
    if isinstance(proposal, dict):
        return dict(proposal)
    if hasattr(proposal, "model_dump"):
        return dict(proposal.model_dump())
    return dict(getattr(proposal, "__dict__", {}))


def clear_latest_controller_chat_snapshot() -> None:
    global _LATEST_CHAT_SNAPSHOT
    _LATEST_CHAT_SNAPSHOT = None


def record_controller_chat_snapshot(message: str, result: Dict[str, Any], model: str = "") -> None:
    global _LATEST_CHAT_SNAPSHOT

    snapshot: Dict[str, Any] = {
        "recorded_at": _utc_now(),
        "message_preview": str(message or "")[:200],
        "model": model,
        "response_type": str(result.get("type") or "text"),
        "intent_meta": deepcopy(result.get("intent_meta")),
        "grounding_meta": deepcopy(result.get("grounding_meta")),
    }

    proposal = result.get("proposal")
    if proposal:
        proposal_data = _proposal_dict(proposal)
        snapshot["proposal"] = {
            "id": proposal_data.get("id"),
            "status": proposal_data.get("status"),
            "target_device": proposal_data.get("target_device"),
            "feature": proposal_data.get("feature"),
            "risk_level": proposal_data.get("risk_level"),
        }

    _LATEST_CHAT_SNAPSHOT = snapshot


def get_latest_controller_chat_snapshot() -> Optional[Dict[str, Any]]:
    return deepcopy(_LATEST_CHAT_SNAPSHOT) if _LATEST_CHAT_SNAPSHOT else None


def summarize_proposals(proposals: Iterable[Any]) -> Dict[str, Any]:
    items = [_proposal_dict(proposal) for proposal in proposals]
    counts_by_status: Dict[str, int] = {}
    recent = []

    for item in items:
        status = str(item.get("status") or "unknown")
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        if len(recent) < 10:
            action_artifact = item.get("action_artifact") if isinstance(item.get("action_artifact"), dict) else {}
            validation = action_artifact.get("validation") if isinstance(action_artifact, dict) else {}
            approval = action_artifact.get("approval") if isinstance(action_artifact, dict) else {}
            recent.append(
                {
                    "id": item.get("id"),
                    "status": status,
                    "target_device": item.get("target_device"),
                    "feature": item.get("feature"),
                    "risk_level": item.get("risk_level"),
                    "job_id": item.get("job_id"),
                    "request_id": item.get("request_id"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "has_validation": isinstance(validation, dict) and bool(validation),
                    "approval_status": approval.get("status") if isinstance(approval, dict) else None,
                }
            )

    return {
        "total": len(items),
        "counts_by_status": counts_by_status,
        "pending_count": counts_by_status.get("pending", 0),
        "recent": recent,
    }


def summarize_validation_outcomes(proposals: Iterable[Any]) -> Dict[str, Any]:
    items = [_proposal_dict(proposal) for proposal in proposals]
    validated = 0
    passed = 0
    failed = 0
    pending_validation = 0
    recent = []

    for item in items:
        action_artifact = item.get("action_artifact") if isinstance(item.get("action_artifact"), dict) else {}
        validation = action_artifact.get("validation") if isinstance(action_artifact, dict) else None
        approval = action_artifact.get("approval") if isinstance(action_artifact, dict) else {}

        if isinstance(validation, dict) and validation:
            validated += 1
            validation_passed = bool(validation.get("passed", False))
            if validation_passed:
                passed += 1
            else:
                failed += 1
        else:
            validation_passed = None
            pending_validation += 1

        if len(recent) < 10:
            recent.append(
                {
                    "proposal_id": item.get("id"),
                    "status": item.get("status"),
                    "target_device": item.get("target_device"),
                    "feature": item.get("feature"),
                    "action_type": action_artifact.get("action_type") if isinstance(action_artifact, dict) else None,
                    "validation_passed": validation_passed,
                    "error_count": len(list((validation or {}).get("errors") or [])),
                    "warning_count": len(list((validation or {}).get("warnings") or [])),
                    "validation_version": (validation or {}).get("version"),
                    "safety_validated": bool((approval or {}).get("safety_validated", False)),
                    "safety_error_count": len(list((approval or {}).get("safety_errors") or [])),
                    "updated_at": item.get("updated_at") or item.get("created_at"),
                }
            )

    return {
        "total_proposals": len(items),
        "validated_proposals": validated,
        "passed": passed,
        "failed": failed,
        "pending_validation": pending_validation,
        "recent": recent,
    }


def build_controller_observability_payload(
    *,
    controller: Optional[Dict[str, Any]] = None,
    context_health: Optional[Dict[str, Any]] = None,
    sync_freshness: Optional[Dict[str, Any]] = None,
    config_snapshots: Optional[Dict[str, Any]] = None,
    proposal_state: Optional[Dict[str, Any]] = None,
    deployment_state: Optional[Dict[str, Any]] = None,
    validation_outcomes: Optional[Dict[str, Any]] = None,
    latest_chat: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "version": CONTROLLER_OBSERVABILITY_VERSION,
        "generated_at": _utc_now(),
        "controller": controller or {},
        "context_health": context_health or {},
        "sync_freshness": sync_freshness or {},
        "config_snapshots": config_snapshots or {},
        "proposal_state": proposal_state or {},
        "deployment_state": deployment_state or {},
        "validation_outcomes": validation_outcomes or {},
        "grounding_metadata": {
            "available": latest_chat is not None,
            "latest": latest_chat,
        },
    }