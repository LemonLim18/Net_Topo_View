from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

from llm.answer_policy import policy_status
from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_KNOWLEDGE,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
    CONTEXT_DOMAIN_SECURITY,
    CONTEXT_DOMAIN_TELEMETRY,
)
from llm.provider import ToolCall


CONTROLLER_FLOW_VERSION = "phase1_step4_v1"


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


def _successful_tool_result(raw: Any) -> bool:
    payload = _decode_payload(raw)
    return not payload.get("error") and not payload.get("controller_policy_error")


def _non_empty_sequence(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0


def _has_authoritative_evidence(tool_name: str, payload: Dict[str, Any], arguments: Dict[str, Any]) -> bool:
    if payload.get("error") or payload.get("controller_policy_error"):
        return False

    if tool_name == "get_inventory_device":
        return isinstance(payload.get("device"), dict) and bool(payload.get("device"))
    if tool_name == "search_inventory_devices":
        return bool(payload.get("total")) or _non_empty_sequence(payload.get("matches"))
    if tool_name == "list_inventory_devices":
        return bool(payload.get("total")) or _non_empty_sequence(payload.get("devices"))
    if tool_name == "get_planned_topology":
        return bool(payload.get("total")) or _non_empty_sequence(payload.get("devices")) or _non_empty_sequence(payload.get("links"))
    if tool_name == "compare_planned_vs_live":
        return any(
            [
                bool(payload.get("total")),
                _non_empty_sequence(payload.get("devices")),
                _non_empty_sequence(payload.get("planned_only")),
                _non_empty_sequence(payload.get("live_only")),
                _non_empty_sequence(payload.get("management_ip_mismatches")),
            ]
        )
    if tool_name == "get_digital_twin":
        return any(
            [
                _non_empty_sequence(payload.get("devices")),
                _non_empty_sequence(payload.get("edges")),
                isinstance(payload.get("summary"), dict) and bool(payload.get("summary")),
            ]
        )
    if tool_name == "get_topology_summary":
        return bool(payload.get("device_count") or payload.get("total_devices") or payload.get("node_count"))
    if tool_name == "get_device_details":
        return bool(payload.get("hostname") or payload.get("device") or payload.get("mgmt_ip"))
    if tool_name == "find_path":
        return _non_empty_sequence(payload.get("path"))
    if tool_name == "list_all_devices":
        return bool(payload.get("total")) or _non_empty_sequence(payload.get("devices"))
    if tool_name == "get_device_telemetry_snapshot":
        return bool(payload.get("total")) or _non_empty_sequence(payload.get("metrics"))
    if tool_name == "get_network_telemetry_summary":
        return isinstance(payload.get("source_metadata"), dict) and isinstance(payload.get("devices"), list)
    if tool_name == "get_device_running_config":
        return bool(payload.get("config") or payload.get("running_config") or payload.get("rendered_config"))
    if tool_name == "get_config_snapshot_summary":
        return isinstance(payload.get("source_metadata"), dict) and isinstance(payload.get("snapshots"), list)
    if tool_name == "get_recent_incidents":
        return "incidents" in payload and isinstance(payload.get("incidents"), list)
    if tool_name in {"get_security_events", "get_device_syslog_events"}:
        return isinstance(payload.get("source_metadata"), dict) and not payload.get("error") and not payload.get("controller_policy_error")
    if tool_name in {"get_knowledge_base_status", "list_templates"}:
        return bool(payload.get("total")) or _non_empty_sequence(payload.get("documents")) or _non_empty_sequence(payload.get("templates"))
    if tool_name == "get_template_schema":
        return isinstance(payload.get("schema"), dict) and bool(payload.get("schema"))
    return True


def _freshness_available(metadata: Any) -> bool:
    return isinstance(metadata, dict) and str(metadata.get("freshness_class") or "unknown") not in {"missing", "unknown"}


def _domains_satisfied_by_tool(tool_name: str, payload: Dict[str, Any], arguments: Dict[str, Any]) -> List[str]:
    if tool_name == "compare_planned_vs_live":
        has_planned = False
        has_live = False
        for device in payload.get("devices", []):
            if not isinstance(device, dict):
                continue
            presence_state = str(device.get("presence_state") or "unknown")
            if presence_state in {"planned_and_live", "planned_only"}:
                has_planned = True
            if presence_state in {"planned_and_live", "live_only"}:
                has_live = True
        domains: List[str] = []
        if has_planned or isinstance(payload.get("inventory_source_metadata"), dict):
            domains.extend([CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY])
        if has_live or isinstance(payload.get("live_source_metadata"), dict):
            domains.append(CONTEXT_DOMAIN_LIVE_TOPOLOGY)
        return domains

    if tool_name == "get_digital_twin":
        freshness = payload.get("freshness") or {}
        domains: List[str] = []
        if _freshness_available(freshness.get("inventory")):
            domains.append(CONTEXT_DOMAIN_INVENTORY)
        if _freshness_available(freshness.get("planned_topology")):
            domains.append(CONTEXT_DOMAIN_PLANNED_TOPOLOGY)
        if _freshness_available(freshness.get("telemetry")):
            domains.append(CONTEXT_DOMAIN_TELEMETRY)
        if _freshness_available(freshness.get("config_history")):
            domains.append(CONTEXT_DOMAIN_CONFIG)
        if _freshness_available(freshness.get("incidents")):
            domains.append(CONTEXT_DOMAIN_INCIDENTS)
        if payload.get("summary", {}).get("live_link_count") or any(
            str(twin.get("presence_state") or "unknown") in {"planned_and_live", "live_only"}
            for twin in payload.get("twins", [])
            if isinstance(twin, dict)
        ):
            if CONTEXT_DOMAIN_LIVE_TOPOLOGY not in domains:
                domains.append(CONTEXT_DOMAIN_LIVE_TOPOLOGY)
        return domains

    return _domains_for_tool(tool_name, arguments)


def _telemetry_fallback_domain_satisfied(
    tool_results: Dict[str, Any],
    self_correction_state: Optional[Dict[str, Any]],
) -> bool:
    if not isinstance(self_correction_state, dict):
        return False

    attempts = self_correction_state.get("attempts") or []
    recovered_missing_telemetry = any(
        isinstance(item, dict)
        and item.get("failure_kind") == "missing_telemetry"
        and item.get("outcome") == "retry_succeeded"
        and any(
            isinstance(action, dict) and action.get("name") == "get_digital_twin"
            for action in item.get("revised_actions") or []
        )
        for item in attempts
    )
    if not recovered_missing_telemetry:
        return False

    digital_twin_payload = _decode_payload(tool_results.get("get_digital_twin"))
    if digital_twin_payload.get("error") or digital_twin_payload.get("controller_policy_error"):
        return False

    freshness = digital_twin_payload.get("freshness") or {}
    telemetry_metadata = freshness.get("telemetry")
    return isinstance(telemetry_metadata, dict) and bool(telemetry_metadata.get("scope") or telemetry_metadata.get("source"))


def _effective_required_domains(
    query_class: str,
    declared_domains: Sequence[str],
    hostnames: Sequence[str],
) -> List[str]:
    if query_class in {"live_only", "troubleshooting", "compliance"} and declared_domains:
        return list(dict.fromkeys(declared_domains))
    if query_class == "inventory_only":
        return [CONTEXT_DOMAIN_INVENTORY]
    if query_class == "planned_vs_live":
        return [CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY, CONTEXT_DOMAIN_LIVE_TOPOLOGY]
    if query_class == "live_only":
        domains = [CONTEXT_DOMAIN_LIVE_TOPOLOGY]
        if hostnames:
            domains.append(CONTEXT_DOMAIN_TELEMETRY)
        return domains
    if query_class == "troubleshooting":
        domains = [CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_TELEMETRY, CONTEXT_DOMAIN_INCIDENTS]
        if hostnames:
            domains.append(CONTEXT_DOMAIN_CONFIG)
        return domains
    if query_class == "change_planning":
        return [CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_CONFIG, CONTEXT_DOMAIN_KNOWLEDGE]
    if query_class == "compliance":
        return [CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_CONFIG, CONTEXT_DOMAIN_KNOWLEDGE]
    if query_class == "knowledge_heavy":
        return [CONTEXT_DOMAIN_KNOWLEDGE]
    if query_class == "general":
        return [CONTEXT_DOMAIN_INVENTORY] if hostnames else [CONTEXT_DOMAIN_LIVE_TOPOLOGY]
    return list(declared_domains)


def _domains_for_tool(tool_name: str, arguments: Dict[str, Any]) -> List[str]:
    if tool_name in {"get_inventory_device", "search_inventory_devices", "list_inventory_devices"}:
        return [CONTEXT_DOMAIN_INVENTORY]
    if tool_name == "get_planned_topology":
        return [CONTEXT_DOMAIN_PLANNED_TOPOLOGY]
    if tool_name == "compare_planned_vs_live":
        return [CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY, CONTEXT_DOMAIN_LIVE_TOPOLOGY]
    if tool_name == "get_digital_twin":
        return [
            CONTEXT_DOMAIN_INVENTORY,
            CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
            CONTEXT_DOMAIN_LIVE_TOPOLOGY,
            CONTEXT_DOMAIN_TELEMETRY,
            CONTEXT_DOMAIN_CONFIG,
            CONTEXT_DOMAIN_INCIDENTS,
        ]
    if tool_name in {"get_topology_summary", "get_device_details", "find_path"}:
        return [CONTEXT_DOMAIN_LIVE_TOPOLOGY]
    if tool_name == "list_all_devices":
        scope = str(arguments.get("scope") or "both").lower()
        if scope == "inventory":
            return [CONTEXT_DOMAIN_INVENTORY]
        if scope == "live":
            return [CONTEXT_DOMAIN_LIVE_TOPOLOGY]
        return [CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_LIVE_TOPOLOGY]
    if tool_name == "get_device_telemetry_snapshot":
        return [CONTEXT_DOMAIN_TELEMETRY]
    if tool_name == "get_network_telemetry_summary":
        return [CONTEXT_DOMAIN_TELEMETRY]
    if tool_name == "get_device_running_config":
        return [CONTEXT_DOMAIN_CONFIG]
    if tool_name == "get_config_snapshot_summary":
        return [CONTEXT_DOMAIN_CONFIG]
    if tool_name == "get_recent_incidents":
        return [CONTEXT_DOMAIN_INCIDENTS]
    if tool_name in {"get_security_events", "get_device_syslog_events"}:
        return [CONTEXT_DOMAIN_SECURITY]
    if tool_name in {"get_knowledge_base_status", "list_templates", "get_template_schema"}:
        return [CONTEXT_DOMAIN_KNOWLEDGE]
    return []


def _dedupe_tool_calls(calls: Iterable[ToolCall], tool_args: Dict[str, Dict[str, Any]]) -> List[ToolCall]:
    deduped: List[ToolCall] = []
    seen = set()
    for call in calls:
        key = (call.name, json.dumps(call.arguments, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        if tool_args.get(call.name) == call.arguments:
            continue
        deduped.append(call)
    return deduped


def _template_knowledge_calls(
    allowed_tools: Sequence[str],
    tool_results: Dict[str, Any],
) -> List[ToolCall]:
    parse_payload = _decode_payload(tool_results.get("parse_intent"))
    feature = parse_payload.get("feature")
    vendor = parse_payload.get("vendor")
    calls: List[ToolCall] = []
    if feature and "get_template_schema" in allowed_tools:
        calls.append(ToolCall(name="get_template_schema", arguments={"feature": feature}))
    if vendor and "list_templates" in allowed_tools:
        calls.append(ToolCall(name="list_templates", arguments={"vendor": vendor}))
    return calls


def build_recovery_tool_calls(
    *,
    user_message: str,
    query_class: str,
    hostnames: Sequence[str],
    missing_domains: Sequence[str],
    allowed_tools: Sequence[str],
    tool_results: Dict[str, Any],
    tool_args: Dict[str, Dict[str, Any]],
) -> List[ToolCall]:
    allowed = set(allowed_tools)
    primary_hostname = hostnames[0] if hostnames else ""
    calls: List[ToolCall] = []

    for domain in missing_domains:
        if domain == CONTEXT_DOMAIN_INVENTORY:
            if primary_hostname and "get_inventory_device" in allowed:
                calls.append(ToolCall(name="get_inventory_device", arguments={"hostname": primary_hostname}))
            elif "search_inventory_devices" in allowed:
                calls.append(ToolCall(name="search_inventory_devices", arguments={"query": user_message, "limit": 5}))
            elif "compare_planned_vs_live" in allowed:
                arguments = {"hostname": primary_hostname} if primary_hostname else {}
                calls.append(ToolCall(name="compare_planned_vs_live", arguments=arguments))
        elif domain == CONTEXT_DOMAIN_PLANNED_TOPOLOGY:
            if "compare_planned_vs_live" in allowed:
                arguments = {"hostname": primary_hostname} if primary_hostname else {}
                calls.append(ToolCall(name="compare_planned_vs_live", arguments=arguments))
            elif "get_planned_topology" in allowed:
                calls.append(ToolCall(name="get_planned_topology", arguments={}))
        elif domain == CONTEXT_DOMAIN_LIVE_TOPOLOGY:
            if primary_hostname and "get_device_details" in allowed:
                calls.append(ToolCall(name="get_device_details", arguments={"hostname": primary_hostname}))
            elif primary_hostname and "get_digital_twin" in allowed:
                calls.append(ToolCall(name="get_digital_twin", arguments={"hostname": primary_hostname}))
            elif "get_topology_summary" in allowed:
                calls.append(ToolCall(name="get_topology_summary", arguments={}))
        elif domain == CONTEXT_DOMAIN_TELEMETRY:
            if primary_hostname and "get_device_telemetry_snapshot" in allowed:
                calls.append(ToolCall(name="get_device_telemetry_snapshot", arguments={"hostname": primary_hostname, "limit": 10}))
            elif "get_network_telemetry_summary" in allowed:
                calls.append(ToolCall(name="get_network_telemetry_summary", arguments={"limit": 20}))
        elif domain == CONTEXT_DOMAIN_CONFIG:
            if primary_hostname and "get_device_running_config" in allowed:
                calls.append(ToolCall(name="get_device_running_config", arguments={"hostname": primary_hostname}))
            elif "get_config_snapshot_summary" in allowed:
                calls.append(ToolCall(name="get_config_snapshot_summary", arguments={"limit": 20}))
        elif domain == CONTEXT_DOMAIN_INCIDENTS:
            if "get_recent_incidents" in allowed:
                calls.append(ToolCall(name="get_recent_incidents", arguments={"limit": 5}))
        elif domain == CONTEXT_DOMAIN_SECURITY:
            if primary_hostname and "get_device_syslog_events" in allowed:
                calls.append(ToolCall(name="get_device_syslog_events", arguments={"device": primary_hostname, "limit": 20}))
            elif "get_security_events" in allowed:
                calls.append(ToolCall(name="get_security_events", arguments={"limit": 10}))
        elif domain == CONTEXT_DOMAIN_KNOWLEDGE:
            calls.extend(_template_knowledge_calls(allowed_tools, tool_results))
            if not calls and "get_knowledge_base_status" in allowed:
                calls.append(ToolCall(name="get_knowledge_base_status", arguments={"limit": 5}))

    return _dedupe_tool_calls(calls, tool_args)


def build_controller_flow_state(
    *,
    user_message: str,
    query_class: str,
    declared_required_domains: Sequence[str],
    hostnames: Sequence[str],
    tool_results: Dict[str, Any],
    tool_args: Dict[str, Dict[str, Any]],
    allowed_tools: Sequence[str],
    answer_policy: Optional[Dict[str, Any]] = None,
    controller_policy: Optional[Dict[str, Any]] = None,
    self_correction_state: Optional[Dict[str, Any]] = None,
    knowledge_used: bool = False,
) -> Dict[str, Any]:
    required_domains = _effective_required_domains(query_class, declared_required_domains, hostnames)
    domain_coverage: Dict[str, List[str]] = {domain: [] for domain in required_domains}
    blocked_tools: List[str] = []

    for tool_name, arguments in tool_args.items():
        payload = _decode_payload(tool_results.get(tool_name))
        if payload.get("controller_policy_error"):
            blocked_tools.append(tool_name)
        if not _successful_tool_result(tool_results.get(tool_name)):
            continue
        if not _has_authoritative_evidence(tool_name, payload, arguments):
            continue
        for domain in _domains_satisfied_by_tool(tool_name, payload, arguments):
            if domain in domain_coverage and tool_name not in domain_coverage[domain]:
                domain_coverage[domain].append(tool_name)

    if (
        CONTEXT_DOMAIN_TELEMETRY in domain_coverage
        and not domain_coverage[CONTEXT_DOMAIN_TELEMETRY]
        and _telemetry_fallback_domain_satisfied(tool_results, self_correction_state)
    ):
        domain_coverage[CONTEXT_DOMAIN_TELEMETRY].append("get_digital_twin")

    if knowledge_used and CONTEXT_DOMAIN_KNOWLEDGE in domain_coverage:
        domain_coverage[CONTEXT_DOMAIN_KNOWLEDGE].append("knowledge_context")

    satisfied_domains = [domain for domain, evidence in domain_coverage.items() if evidence]
    missing_domains = [domain for domain in required_domains if domain not in satisfied_domains]

    answer_policy_status = policy_status(answer_policy or {}, tool_args)
    controller_policy = controller_policy or {}
    required_sequence = list(controller_policy.get("required_sequence") or [])
    completed_sequence = [step for step in required_sequence if _successful_tool_result(tool_results.get(step))]
    missing_sequence = [step for step in required_sequence if step not in completed_sequence]

    recovery_tool_calls = build_recovery_tool_calls(
        user_message=user_message,
        query_class=query_class,
        hostnames=hostnames,
        missing_domains=missing_domains,
        allowed_tools=allowed_tools,
        tool_results=tool_results,
        tool_args=tool_args,
    )

    if not answer_policy_status["satisfied"]:
        decision = "reroute"
        decision_reason = "Required authoritative tool calls have not all completed."
    elif missing_sequence:
        decision = "reroute"
        decision_reason = "Required controller workflow steps are incomplete."
    elif missing_domains and recovery_tool_calls:
        decision = "reroute"
        decision_reason = "Required evidence domains are missing but additional authoritative reads are available."
    elif missing_domains:
        decision = "block"
        decision_reason = "Required authoritative evidence could not be satisfied for this request."
    else:
        decision = "allow"
        decision_reason = "Required reasoning, action, and observation checks are satisfied."

    return {
        "version": CONTROLLER_FLOW_VERSION,
        "mode": "reason_act_observe",
        "query_class": query_class,
        "reason": {
            "user_message": user_message,
            "declared_required_domains": list(declared_required_domains),
            "required_domains": required_domains,
            "hostnames": list(hostnames),
            "answer_policy": answer_policy_status,
            "controller_policy": {
                "requires_change_workflow": bool(controller_policy.get("requires_change_workflow")),
                "required_sequence": required_sequence,
            },
        },
        "act": {
            "observed_tool_calls": sorted(tool_args.keys()),
            "blocked_tool_calls": blocked_tools,
        },
        "observe": {
            "domain_coverage": domain_coverage,
            "satisfied_domains": satisfied_domains,
            "missing_domains": missing_domains,
            "change_workflow": {
                "completed_steps": completed_sequence,
                "missing_steps": missing_sequence,
                "satisfied": not missing_sequence,
            },
            "self_correction": self_correction_state or {},
        },
        "release": {
            "decision": decision,
            "reason": decision_reason,
            "recovery_tool_calls": [
                {"name": call.name, "arguments": call.arguments}
                for call in recovery_tool_calls
            ],
            "missing_domains": missing_domains,
            "missing_required_tools": list(answer_policy_status.get("missing_required_tools") or []),
            "missing_change_steps": missing_sequence,
        },
    }
