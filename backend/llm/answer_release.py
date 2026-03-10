from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from llm.provider import ToolCall


ANSWER_RELEASE_VERSION = "phase2_step8_v1"

_REFRESHABLE_FRESHNESS = {"stale", "missing"}
_LIVE_REFRESH_SCOPES = {
    "live_topology",
    "telemetry",
    "config_history",
    "incidents",
    "drift",
    "security_alerts",
    "incident_state",
    "drift_state",
    "device_configs",
    "change_management",
    "deployment_status",
    "sdn_state",
    "device_metrics",
}
_PLANNED_REFRESH_TARGET_BY_SCOPE = {
    "planned_inventory": "inventory",
    "planned_topology": "planned_topology",
}


def _refresh_target_for_scope(scope: str) -> Optional[str]:
    normalized = str(scope or "unknown")
    if normalized in _PLANNED_REFRESH_TARGET_BY_SCOPE:
        return _PLANNED_REFRESH_TARGET_BY_SCOPE[normalized]
    if normalized in _LIVE_REFRESH_SCOPES:
        return "live_topology"
    return None


def _dedupe_tool_calls(calls: Sequence[ToolCall]) -> List[ToolCall]:
    deduped: List[ToolCall] = []
    seen = set()
    for call in calls:
        key = (call.name, tuple(sorted(call.arguments.items())))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call)
    return deduped


def _refresh_tool_calls(
    grounding: Dict[str, Any],
    tool_args: Dict[str, Dict[str, Any]],
    allowed_tools: Sequence[str],
) -> List[ToolCall]:
    allowed = {str(name) for name in allowed_tools or []}
    if "trigger_context_refresh" not in allowed or "trigger_context_refresh" in tool_args:
        return []

    targets: List[str] = []
    for entry in grounding.get("source_age") or []:
        if str(entry.get("freshness_class") or "unknown") not in _REFRESHABLE_FRESHNESS:
            continue
        target = _refresh_target_for_scope(str(entry.get("scope") or "unknown"))
        if target:
            targets.append(target)

    normalized_targets = sorted(set(targets))
    if not normalized_targets:
        return []

    calls: List[ToolCall] = []
    if "get_last_sync_status" in allowed and "get_last_sync_status" not in tool_args:
        calls.append(ToolCall(name="get_last_sync_status", arguments={}))

    if len(normalized_targets) > 1:
        calls.append(ToolCall(name="trigger_context_refresh", arguments={"target": "all", "wait": False}))
    else:
        calls.append(
            ToolCall(name="trigger_context_refresh", arguments={"target": normalized_targets[0], "wait": False})
        )
    return _dedupe_tool_calls(calls)


def build_answer_release_state(
    *,
    query_class: str,
    grounding: Dict[str, Any],
    tool_args: Optional[Dict[str, Dict[str, Any]]] = None,
    allowed_tools: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    tool_args = tool_args or {}
    allowed_tools = allowed_tools or []

    freshness_class = str(grounding.get("freshness_class") or "unknown")
    conflicts = grounding.get("conflicts") or []
    evidence_authority = grounding.get("evidence_authority") or {}
    stale_entries = [
        entry for entry in grounding.get("source_age") or []
        if str(entry.get("freshness_class") or "unknown") in _REFRESHABLE_FRESHNESS
    ]
    conflict_codes = [str(conflict.get("code") or "unknown") for conflict in conflicts]
    refresh_calls = _refresh_tool_calls(grounding, tool_args, allowed_tools)
    refresh_attempted = "trigger_context_refresh" in tool_args
    substitution_risk = bool(evidence_authority.get("substitution_risk"))
    preferred_evidence_present = bool(evidence_authority.get("preferred_evidence_present", True))

    degraded_signals: List[str] = []
    if stale_entries or freshness_class in _REFRESHABLE_FRESHNESS:
        degraded_signals.append("stale_or_missing_evidence")
    if conflicts:
        degraded_signals.append("conflicting_evidence")
    if substitution_risk or not preferred_evidence_present:
        degraded_signals.append("authority_gap")

    if stale_entries and refresh_calls and not refresh_attempted:
        decision = "reroute"
        reason = "Required evidence is stale or missing, so the controller requested a refresh before releasing the answer."
    elif degraded_signals:
        decision = "allow_limited"
        reason_parts: List[str] = []
        if stale_entries or freshness_class in _REFRESHABLE_FRESHNESS:
            reason_parts.append("Some required evidence is stale or missing")
        if conflicts:
            reason_parts.append("authoritative sources disagree")
        if substitution_risk or not preferred_evidence_present:
            reason_parts.append("the preferred evidence family is incomplete")
        reason = "; ".join(reason_parts) + "."
    else:
        decision = "allow"
        reason = "Grounding freshness, authority, and conflict checks allow release."

    return {
        "version": ANSWER_RELEASE_VERSION,
        "query_class": query_class,
        "decision": decision,
        "reason": reason,
        "limited": decision == "allow_limited",
        "refresh_recommended": bool(refresh_calls) or bool(stale_entries),
        "refresh_attempted": refresh_attempted,
        "refresh_tool_calls": [
            {"name": call.name, "arguments": call.arguments}
            for call in refresh_calls
        ],
        "degraded_signals": degraded_signals,
        "conflict_codes": conflict_codes,
        "stale_sources": [
            {
                "source": entry.get("source"),
                "scope": entry.get("scope"),
                "freshness_class": entry.get("freshness_class"),
            }
            for entry in stale_entries
        ],
        "authority_summary": str(evidence_authority.get("summary") or ""),
    }