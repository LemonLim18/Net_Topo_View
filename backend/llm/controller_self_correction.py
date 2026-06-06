from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from llm.provider import ToolCall


SELF_CORRECTION_VERSION = "phase1_step5_v1"
DEFAULT_SELF_CORRECTION_MAX_ATTEMPTS = 2

_MALFORMED_ARGUMENT_PATTERNS = (
    r"unexpected keyword argument",
    r"missing \d+ required positional argument",
    r"missing 1 required positional argument",
    r"got an unexpected keyword",
    r"takes .* positional argument",
)
_SCHEMA_PATTERNS = (
    r"schema",
    r"required property",
    r"required parameter",
    r"validation",
    r"missing required",
)
_SYNTAX_PATTERNS = (
    r"syntax",
    r"yaml",
    r"parse",
    r"ansible-lint",
    r"lint",
)
_DRY_RUN_PATTERNS = (
    r"dry-run",
    r"check mode",
    r"--check",
)


def _decode_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _error_text(payload: Dict[str, Any]) -> str:
    parts = [
        str(payload.get("error") or ""),
        str(payload.get("message") or ""),
        str(payload.get("raw") or ""),
    ]
    return " ".join(part for part in parts if part).strip().lower()


def _matches_any(patterns: Sequence[str], text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def build_self_correction_state(max_attempts: int = DEFAULT_SELF_CORRECTION_MAX_ATTEMPTS) -> Dict[str, Any]:
    return {
        "version": SELF_CORRECTION_VERSION,
        "max_attempts": max_attempts,
        "attempts": [],
        "escalations": [],
    }


def _attempt_count(state: Dict[str, Any], tool_name: str, failure_kind: str) -> int:
    count = 0
    for item in reversed(state.get("attempts", [])):
        if item.get("tool_name") != tool_name or item.get("failure_kind") != failure_kind:
            continue
        if item.get("outcome") == "retry_succeeded":
            break
        count += 1
    return count


def _candidate_hostname(route_plan: Dict[str, Any], tool_results: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    hostname = arguments.get("hostname") if isinstance(arguments, dict) else ""
    if isinstance(hostname, str) and hostname.strip():
        return hostname.strip()

    hostnames = route_plan.get("hostnames") or []
    if hostnames:
        return str(hostnames[0])

    parse_payload = _decode_payload(tool_results.get("parse_intent"))
    scope = parse_payload.get("scope") or {}
    if isinstance(scope, dict):
        devices = scope.get("device_hostnames") or []
        if devices:
            return str(devices[0])
    resolved = parse_payload.get("resolved_devices") or []
    if resolved:
        return str(resolved[0])
    return ""


def _candidate_feature(tool_results: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    feature = arguments.get("feature") if isinstance(arguments, dict) else ""
    if isinstance(feature, str) and feature.strip():
        return feature.strip()
    parse_payload = _decode_payload(tool_results.get("parse_intent"))
    return str(parse_payload.get("feature") or "")


def _candidate_vendor(tool_results: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    vendor = arguments.get("vendor") if isinstance(arguments, dict) else ""
    if isinstance(vendor, str) and vendor.strip():
        return vendor.strip()
    parse_payload = _decode_payload(tool_results.get("parse_intent"))
    return str(parse_payload.get("vendor") or "")


def classify_failure_kind(tool_name: str, raw_result: Any) -> Optional[str]:
    payload = _decode_payload(raw_result)
    error_text = _error_text(payload)

    if not error_text and tool_name != "get_device_telemetry_snapshot":
        return None

    if tool_name == "get_device_telemetry_snapshot" and (
        error_text or payload.get("freshness_class") == "missing" or payload.get("sample_count") == 0
    ):
        return "missing_telemetry"
    if _matches_any(_MALFORMED_ARGUMENT_PATTERNS, error_text):
        return "malformed_arguments"
    if _matches_any(_DRY_RUN_PATTERNS, error_text):
        return "dry_run_failure"
    if _matches_any(_SYNTAX_PATTERNS, error_text):
        if "playbook" in tool_name:
            return "invalid_playbook"
        return "syntax_failure"
    if _matches_any(_SCHEMA_PATTERNS, error_text):
        return "schema_violation"
    if error_text:
        return "read_failure"
    return None


def _repair_for_malformed_arguments(
    tool_name: str,
    route_plan: Dict[str, Any],
    tool_results: Dict[str, Any],
    arguments: Dict[str, Any],
) -> List[ToolCall]:
    hostname = _candidate_hostname(route_plan, tool_results, arguments)
    feature = _candidate_feature(tool_results, arguments)
    vendor = _candidate_vendor(tool_results, arguments)

    if tool_name in {"get_inventory_device", "get_device_details", "get_device_running_config", "get_digital_twin"} and hostname:
        return [ToolCall(name=tool_name, arguments={"hostname": hostname})]
    if tool_name == "get_device_telemetry_snapshot" and hostname:
        return [ToolCall(name=tool_name, arguments={"hostname": hostname, "limit": 10})]
    if tool_name == "compare_planned_vs_live":
        return [ToolCall(name=tool_name, arguments={"hostname": hostname} if hostname else {})]
    if tool_name == "get_recent_drift_events" and hostname:
        return [ToolCall(name=tool_name, arguments={"hostname": hostname, "limit": 5})]
    if tool_name == "get_template_schema" and feature:
        return [ToolCall(name=tool_name, arguments={"feature": feature})]
    if tool_name == "list_templates" and vendor:
        return [ToolCall(name=tool_name, arguments={"vendor": vendor})]
    return []


def _repair_for_missing_telemetry(
    route_plan: Dict[str, Any],
    tool_results: Dict[str, Any],
    arguments: Dict[str, Any],
    allowed_tools: Sequence[str],
) -> List[ToolCall]:
    hostname = _candidate_hostname(route_plan, tool_results, arguments)
    calls: List[ToolCall] = []
    limit = arguments.get("limit") if isinstance(arguments, dict) else None
    if hostname and isinstance(limit, int) and limit > 5:
        calls.append(ToolCall(name="get_device_telemetry_snapshot", arguments={"hostname": hostname, "limit": 5}))
    if hostname and "get_digital_twin" in allowed_tools:
        calls.append(ToolCall(name="get_digital_twin", arguments={"hostname": hostname}))
    if "get_recent_incidents" in allowed_tools:
        calls.append(ToolCall(name="get_recent_incidents", arguments={"limit": 5}))
    return calls


def _repair_for_read_failure(
    tool_name: str,
    user_message: str,
    route_plan: Dict[str, Any],
    tool_results: Dict[str, Any],
    allowed_tools: Sequence[str],
) -> List[ToolCall]:
    hostname = _candidate_hostname(route_plan, tool_results, {})
    if tool_name == "get_inventory_device":
        if hostname and "search_inventory_devices" in allowed_tools:
            return [ToolCall(name="search_inventory_devices", arguments={"query": hostname, "limit": 5})]
        if "search_inventory_devices" in allowed_tools:
            return [ToolCall(name="search_inventory_devices", arguments={"query": user_message, "limit": 5})]
        if "compare_planned_vs_live" in allowed_tools:
            return [ToolCall(name="compare_planned_vs_live", arguments={"hostname": hostname} if hostname else {})]
    if tool_name == "get_device_details":
        if hostname and "get_digital_twin" in allowed_tools:
            return [ToolCall(name="get_digital_twin", arguments={"hostname": hostname})]
        if "get_topology_summary" in allowed_tools:
            return [ToolCall(name="get_topology_summary", arguments={})]
    if tool_name == "compare_planned_vs_live":
        calls: List[ToolCall] = []
        if hostname and "get_inventory_device" in allowed_tools:
            calls.append(ToolCall(name="get_inventory_device", arguments={"hostname": hostname}))
        if hostname and "get_device_details" in allowed_tools:
            calls.append(ToolCall(name="get_device_details", arguments={"hostname": hostname}))
        return calls
    if tool_name == "get_device_running_config" and hostname and "get_digital_twin" in allowed_tools:
        return [ToolCall(name="get_digital_twin", arguments={"hostname": hostname})]
    return []


def _repair_for_schema_or_plan_failure(
    tool_results: Dict[str, Any],
    allowed_tools: Sequence[str],
) -> List[ToolCall]:
    feature = _candidate_feature(tool_results, {})
    vendor = _candidate_vendor(tool_results, {})
    calls: List[ToolCall] = []
    if feature and "get_template_schema" in allowed_tools:
        calls.append(ToolCall(name="get_template_schema", arguments={"feature": feature}))
    if vendor and "list_templates" in allowed_tools:
        calls.append(ToolCall(name="list_templates", arguments={"vendor": vendor}))
    return calls


def plan_self_correction(
    *,
    user_message: str,
    tool_name: str,
    arguments: Dict[str, Any],
    raw_result: Any,
    route_plan: Dict[str, Any],
    tool_results: Dict[str, Any],
    state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    failure_kind = classify_failure_kind(tool_name, raw_result)
    if failure_kind is None:
        return None

    attempt_number = _attempt_count(state, tool_name, failure_kind) + 1
    payload = _decode_payload(raw_result)
    error_text = _error_text(payload)
    allowed_tools = route_plan.get("allowed_tools") or []

    revised_tool_calls: List[ToolCall] = []
    if failure_kind == "malformed_arguments":
        revised_tool_calls = _repair_for_malformed_arguments(tool_name, route_plan, tool_results, arguments)
    elif failure_kind == "missing_telemetry":
        revised_tool_calls = _repair_for_missing_telemetry(route_plan, tool_results, arguments, allowed_tools)
    elif failure_kind == "read_failure":
        revised_tool_calls = _repair_for_read_failure(tool_name, user_message, route_plan, tool_results, allowed_tools)
    elif failure_kind in {"schema_violation", "syntax_failure", "invalid_playbook", "dry_run_failure"}:
        revised_tool_calls = _repair_for_schema_or_plan_failure(tool_results, allowed_tools)

    deduped_calls: List[ToolCall] = []
    seen = set()
    for call in revised_tool_calls:
        key = (call.name, json.dumps(call.arguments, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        deduped_calls.append(call)
    revised_tool_calls = deduped_calls

    entry = {
        "tool_name": tool_name,
        "failure_kind": failure_kind,
        "attempt": attempt_number,
        "observed_failure": error_text or f"{tool_name} returned incomplete evidence",
        "original_arguments": dict(arguments or {}),
        "revised_actions": [{"name": call.name, "arguments": call.arguments} for call in revised_tool_calls],
    }

    if attempt_number > int(state.get("max_attempts") or DEFAULT_SELF_CORRECTION_MAX_ATTEMPTS):
        escalation_reason = f"Retry cap reached for {tool_name} after {attempt_number - 1} prior attempt(s)."
        entry["outcome"] = "escalated"
        entry["escalation_reason"] = escalation_reason
        return {
            "decision": "escalate",
            "entry": entry,
            "escalation_reason": escalation_reason,
            "revised_tool_calls": [],
        }

    if not revised_tool_calls:
        escalation_reason = f"No safe self-correction path was available for {tool_name} ({failure_kind})."
        entry["outcome"] = "escalated"
        entry["escalation_reason"] = escalation_reason
        return {
            "decision": "escalate",
            "entry": entry,
            "escalation_reason": escalation_reason,
            "revised_tool_calls": [],
        }

    return {
        "decision": "retry",
        "entry": entry,
        "revised_tool_calls": revised_tool_calls,
    }