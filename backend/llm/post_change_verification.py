from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from llm import network_tools


POST_CHANGE_VERIFICATION_VERSION = "phase5_step15_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tool_payload(tool_func, *args, **kwargs) -> Dict[str, Any]:
    raw = tool_func(*args, **kwargs)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"message": raw}
    if isinstance(raw, dict):
        return raw
    return {"value": raw}


def _proposal_data(proposal: Any) -> Dict[str, Any]:
    if isinstance(proposal, dict):
        return dict(proposal)
    if hasattr(proposal, "model_dump"):
        return dict(proposal.model_dump())
    return dict(getattr(proposal, "__dict__", {}))


def _normalize_config_lines(config_text: str) -> list[str]:
    lines = []
    for line in str(config_text or "").splitlines():
        normalized = " ".join(line.strip().split())
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in {"!", "end", "exit"}:
            continue
        if normalized.startswith("#"):
            continue
        lines.append(normalized)
    return lines


def _related_hostname(hostname: str, row: Dict[str, Any]) -> bool:
    hostname_lower = str(hostname or "").lower()
    if not hostname_lower:
        return False

    direct_values = [
        row.get("hostname"),
        row.get("target_device"),
        row.get("device"),
        row.get("source"),
    ]
    if any(str(value or "").lower() == hostname_lower for value in direct_values):
        return True

    affected_devices = row.get("affected_devices")
    if isinstance(affected_devices, str):
        try:
            affected_devices = json.loads(affected_devices)
        except json.JSONDecodeError:
            affected_devices = [affected_devices]

    if isinstance(affected_devices, Iterable) and not isinstance(affected_devices, (str, bytes, dict)):
        return any(str(value or "").lower() == hostname_lower for value in affected_devices)

    return False


def _latest_job_state(pipeline: Any, job_id: Optional[int]) -> Dict[str, Any]:
    if not pipeline or not job_id:
        return {"available": False, "reason": "deployment job unavailable"}

    job_row = None
    try:
        for row in pipeline.get_all_jobs():
            if row.get("id") == job_id:
                job_row = dict(row)
                break
    except Exception as exc:
        return {"available": False, "reason": str(exc)}

    if not job_row:
        return {"available": False, "reason": f"job {job_id} not found"}

    try:
        logs = pipeline.get_job_logs(job_id)
    except Exception as exc:
        logs = [{"stage": "unknown", "details": f"failed to load logs: {exc}"}]

    job_row["available"] = True
    job_row["logs"] = logs[-10:]
    return job_row


def _config_presence_check(hostname: str, generated_config: str) -> Dict[str, Any]:
    payload = _tool_payload(network_tools.get_device_running_config, hostname)
    observed_config = str(payload.get("config") or "")
    expected_lines = _normalize_config_lines(generated_config)
    observed_lines = _normalize_config_lines(observed_config)
    observed_set = set(observed_lines)
    matched = [line for line in expected_lines if line in observed_set]

    if payload.get("error"):
        return {
            "status": "unavailable",
            "reason": str(payload.get("error")),
            "snapshot_time": payload.get("snapshot_time"),
            "matched_line_count": 0,
            "expected_line_count": len(expected_lines),
        }

    if not expected_lines:
        return {
            "status": "unavailable",
            "reason": "No generated configuration lines were available for comparison.",
            "snapshot_time": payload.get("snapshot_time"),
            "matched_line_count": 0,
            "expected_line_count": 0,
        }

    missing = [line for line in expected_lines if line not in observed_set]
    if not missing:
        return {
            "status": "pass",
            "reason": "All generated configuration lines were present in the latest running-config snapshot.",
            "snapshot_time": payload.get("snapshot_time"),
            "matched_line_count": len(matched),
            "expected_line_count": len(expected_lines),
        }

    return {
        "status": "fail",
        "reason": "One or more generated configuration lines were not found in the latest running-config snapshot.",
        "snapshot_time": payload.get("snapshot_time"),
        "matched_line_count": len(matched),
        "expected_line_count": len(expected_lines),
        "missing_lines": missing[:10],
    }


def _command_success_check(job_state: Dict[str, Any]) -> Dict[str, Any]:
    if not job_state.get("available"):
        return {"status": "unavailable", "reason": job_state.get("reason", "job state unavailable")}

    status = str(job_state.get("status") or "unknown").lower()
    stage = str(job_state.get("stage") or "unknown")
    error_message = job_state.get("error_message")
    if status == "success":
        return {
            "status": "pass",
            "reason": "Deployment pipeline completed successfully.",
            "job_status": status,
            "stage": stage,
        }
    if status in {"failed", "rolled_back"}:
        return {
            "status": "fail",
            "reason": str(error_message or "Deployment pipeline reported a failed execution outcome."),
            "job_status": status,
            "stage": stage,
        }
    return {
        "status": "warn",
        "reason": f"Deployment pipeline finished in status '{status}' at stage '{stage}'.",
        "job_status": status,
        "stage": stage,
    }


def _telemetry_outcome_check(hostname: str) -> Dict[str, Any]:
    payload = _tool_payload(network_tools.get_device_telemetry_snapshot, hostname)
    if payload.get("error"):
        return {
            "status": "unavailable",
            "reason": str(payload.get("error")),
            "freshness": (payload.get("source_metadata") or {}).get("freshness_class"),
            "sample_count": int(payload.get("total") or 0),
        }

    freshness = str((payload.get("source_metadata") or {}).get("freshness_class") or "unknown")
    sample_count = int(payload.get("total") or 0)
    if sample_count <= 0:
        return {
            "status": "warn",
            "reason": "No telemetry samples were available after deployment.",
            "freshness": freshness,
            "sample_count": sample_count,
        }
    if freshness in {"live", "warm"}:
        return {
            "status": "pass",
            "reason": "Telemetry samples are available with acceptable freshness.",
            "freshness": freshness,
            "sample_count": sample_count,
        }
    return {
        "status": "warn",
        "reason": f"Telemetry is available but freshness is '{freshness}', so post-change state should be reviewed.",
        "freshness": freshness,
        "sample_count": sample_count,
    }


def _incident_impact_check(hostname: str) -> Dict[str, Any]:
    payload = _tool_payload(network_tools.get_recent_incidents, 20, "")
    if payload.get("error"):
        return {
            "status": "unavailable",
            "reason": str(payload.get("error")),
            "active_incident_count": 0,
        }

    incidents = [row for row in payload.get("incidents", []) if isinstance(row, dict) and _related_hostname(hostname, row)]
    active = [row for row in incidents if str(row.get("status") or "open").lower() not in {"resolved", "dismissed", "closed"}]

    if active:
        return {
            "status": "fail",
            "reason": f"Found {len(active)} active incident(s) related to the target device after deployment.",
            "active_incident_count": len(active),
            "incident_ids": [row.get("id") for row in active[:10]],
        }
    return {
        "status": "pass",
        "reason": "No active incidents were found for the target device in the recent incident window.",
        "active_incident_count": 0,
    }


def _drift_state_check(hostname: str) -> Dict[str, Any]:
    payload = _tool_payload(network_tools.get_recent_drift_events, 20, hostname)
    if payload.get("error"):
        return {
            "status": "unavailable",
            "reason": str(payload.get("error")),
            "drift_event_count": 0,
        }

    events = [row for row in payload.get("events", []) if isinstance(row, dict)]
    active = [row for row in events if bool(row.get("has_drift"))]
    if active:
        severities = sorted({str(row.get("severity") or "unknown") for row in active})
        return {
            "status": "fail",
            "reason": f"Detected {len(active)} post-change drift event(s) for the target device.",
            "drift_event_count": len(active),
            "severities": severities,
        }
    return {
        "status": "pass",
        "reason": "No post-change drift events were found for the target device.",
        "drift_event_count": 0,
    }


def _rollback_available(proposal_data: Dict[str, Any]) -> bool:
    if str(proposal_data.get("rollback_config") or "").strip():
        return True
    action_artifact = proposal_data.get("action_artifact") if isinstance(proposal_data.get("action_artifact"), dict) else {}
    rollback_intent = action_artifact.get("rollback_intent") if isinstance(action_artifact, dict) else {}
    if isinstance(rollback_intent, dict) and int(rollback_intent.get("available_device_count") or 0) > 0:
        return True
    for item in action_artifact.get("device_artifacts") or []:
        if str(item.get("rollback_config") or "").strip():
            return True
        if isinstance(item.get("rollback_plan"), dict) and item["rollback_plan"].get("runtime_restore_available"):
            return True
    return False


def build_post_change_verification_report(
    proposal: Any,
    *,
    job_id: Optional[int] = None,
    request_id: Optional[int] = None,
    pipeline: Any = None,
    db_manager: Any = None,
) -> Dict[str, Any]:
    proposal_data = _proposal_data(proposal)
    hostname = str(proposal_data.get("target_device") or "")
    if db_manager is not None:
        network_tools.set_db_manager(db_manager)

    job_state = _latest_job_state(pipeline, job_id)
    checks = {
        "config_presence": _config_presence_check(hostname, str(proposal_data.get("generated_config") or "")),
        "command_success": _command_success_check(job_state),
        "telemetry_outcome": _telemetry_outcome_check(hostname),
        "incident_impact": _incident_impact_check(hostname),
        "drift_state": _drift_state_check(hostname),
    }

    failed_checks = [name for name, item in checks.items() if item.get("status") == "fail"]
    warning_checks = [name for name, item in checks.items() if item.get("status") in {"warn", "unavailable"}]

    if failed_checks:
        outcome = "failed"
    elif warning_checks:
        outcome = "needs_review"
    else:
        outcome = "passed"

    rollback_available = _rollback_available(proposal_data)
    command_status = checks["command_success"].get("status")
    job_status = str(job_state.get("status") or "unknown").lower() if job_state.get("available") else "unknown"

    correction_needed = outcome != "passed"
    correction_recommendation = {
        "needed": correction_needed,
        "summary": (
            "Review the post-change findings, refresh live topology and telemetry, inspect deployment job logs, and reconcile the running configuration against the approved artifact."
            if correction_needed
            else "No corrective action is required based on the available post-change evidence."
        ),
        "next_steps": (
            [
                "Inspect deployment job logs and failed verification stages.",
                "Refresh live topology, telemetry, incidents, and drift evidence before any follow-up action.",
                "Compare the latest running-config snapshot with the approved generated configuration.",
            ]
            if correction_needed
            else ["Continue monitoring telemetry and incident surfaces for delayed regressions."]
        ),
    }

    rollback_recommendation = {
        "recommended": bool(failed_checks and rollback_available),
        "available": rollback_available,
        "summary": (
            "Rollback data is available and should be reviewed for execution because post-change verification failed."
            if failed_checks and rollback_available
            else (
                "Rollback was already attempted by the deployment pipeline; review the restore outcome and remaining findings."
                if job_status == "rolled_back"
                else "Rollback is not currently recommended based on the available evidence."
            )
        ),
    }

    if outcome == "passed":
        recommended_action = {
            "action": "monitor",
            "reason": "Post-change checks passed across config, command execution, incidents, and drift surfaces.",
        }
    elif job_status == "rolled_back":
        recommended_action = {
            "action": "manual_correction",
            "reason": "The deployment pipeline rolled the change back; inspect the rollback outcome and root cause before retrying.",
        }
    elif failed_checks and rollback_available:
        recommended_action = {
            "action": "rollback_recommended",
            "reason": "Post-change verification failed and a rollback path is available.",
        }
    else:
        recommended_action = {
            "action": "correction_recommended",
            "reason": "Post-change evidence is incomplete or indicates issues that require manual correction review.",
        }

    return {
        "version": POST_CHANGE_VERIFICATION_VERSION,
        "generated_at": _utc_now(),
        "proposal_id": proposal_data.get("id"),
        "request_id": request_id,
        "job_id": job_id,
        "hostname": hostname,
        "outcome": outcome,
        "deployment_job": {
            "available": bool(job_state.get("available")),
            "status": job_state.get("status"),
            "stage": job_state.get("stage"),
            "error_message": job_state.get("error_message"),
            "logs": job_state.get("logs") or [],
        },
        "checks": checks,
        "failed_checks": failed_checks,
        "warning_checks": warning_checks,
        "recommended_action": recommended_action,
        "correction_recommendation": correction_recommendation,
        "rollback_recommendation": rollback_recommendation,
    }