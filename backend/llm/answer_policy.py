from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from llm.provider import ToolCall


_MANAGEMENT_IP_PATTERN = re.compile(r"\b(management\s+ip|mgmt\s+ip|primary\s+ip)\b", re.IGNORECASE)
_LIVE_COMPARE_HINT_PATTERN = re.compile(r"\b(live|actual|currently|right now|compare|match|mismatch)\b", re.IGNORECASE)
_SYSLOG_FOCUS_PATTERN = re.compile(
    r"\b(syslog|syslogs|security event|security events|security log|security logs|log entry|log entries)\b",
    re.IGNORECASE,
)
_TELEMETRY_HINT_PATTERN = re.compile(
    r"\b(cpu|memory|mem|telemetry|metric|metrics|traffic|utilization|usage|packet loss|latency|counters|statistics|stats)\b",
    re.IGNORECASE,
)
_CONFIG_FOCUS_PATTERN = re.compile(
    r"\b(config backup|config backups|configuration backup|configuration backups|config history|running config|running configuration|config snapshot|config snapshots|latest config|last config|previous config|show config)\b",
    re.IGNORECASE,
)


def build_answer_policy(
    user_message: str,
    query_class: str,
    hostnames: Optional[List[str]] = None,
) -> Dict[str, Any]:
    hostnames = hostnames or []
    primary_hostname = hostnames[0] if hostnames else ""
    normalized_message = user_message or ""

    policy = {
        "name": "default_grounding",
        "reason": "General authoritative grounding policy.",
        "required_tool_calls": [],
        "recommended_tool_calls": [],
    }

    if primary_hostname and _MANAGEMENT_IP_PATTERN.search(normalized_message):
        policy["name"] = "management_ip_lookup"
        policy["reason"] = "Management IP answers must check source-of-truth inventory before answering."
        policy["required_tool_calls"] = [
            ToolCall(name="get_inventory_device", arguments={"hostname": primary_hostname}),
        ]
        if _LIVE_COMPARE_HINT_PATTERN.search(normalized_message):
            policy["recommended_tool_calls"] = [
                ToolCall(name="compare_planned_vs_live", arguments={"hostname": primary_hostname}),
            ]
        return policy

    if query_class == "planned_vs_live":
        policy["name"] = "planned_vs_live_compare"
        policy["reason"] = "Planned-vs-live answers must compare authoritative planned state with live discovery."
        policy["required_tool_calls"] = [
            ToolCall(name="compare_planned_vs_live", arguments={"hostname": primary_hostname} if primary_hostname else {}),
        ]
        return policy

    if query_class == "live_only":
        if _TELEMETRY_HINT_PATTERN.search(normalized_message):
            policy["name"] = "telemetry_lookup"
            policy["reason"] = "Telemetry-focused answers must check current metrics evidence before answering."
            policy["required_tool_calls"] = [
                ToolCall(name="get_device_telemetry_snapshot", arguments={"hostname": primary_hostname, "limit": 10})
                if primary_hostname
                else ToolCall(name="get_network_telemetry_summary", arguments={"limit": 20}),
            ]
        else:
            policy["name"] = "live_state_lookup"
            policy["reason"] = "Live-state answers must check live topology evidence before answering."
            policy["required_tool_calls"] = [
                ToolCall(name="get_device_details", arguments={"hostname": primary_hostname}) if primary_hostname else ToolCall(name="get_topology_summary", arguments={}),
            ]
        return policy

    if query_class == "troubleshooting" and _SYSLOG_FOCUS_PATTERN.search(normalized_message) and not _TELEMETRY_HINT_PATTERN.search(normalized_message):
        policy["name"] = "syslog_lookup"
        policy["reason"] = "Syslog-focused questions should be grounded in security events and device log evidence without forcing telemetry."
        if primary_hostname:
            policy["required_tool_calls"] = [
                ToolCall(name="get_device_syslog_events", arguments={"device": primary_hostname, "limit": 20}),
            ]
        else:
            policy["required_tool_calls"] = [
                ToolCall(name="get_security_events", arguments={"limit": 10}),
            ]
        return policy

    if query_class == "troubleshooting" and primary_hostname:

        policy["name"] = "troubleshooting_multisource"
        policy["reason"] = "Troubleshooting answers must gather inventory, live, telemetry, and config evidence before answering."
        policy["required_tool_calls"] = [
            ToolCall(name="get_inventory_device", arguments={"hostname": primary_hostname}),
            ToolCall(name="get_device_details", arguments={"hostname": primary_hostname}),
            ToolCall(name="get_device_telemetry_snapshot", arguments={"hostname": primary_hostname, "limit": 10}),
            ToolCall(name="get_device_running_config", arguments={"hostname": primary_hostname}),
        ]
        policy["recommended_tool_calls"] = [
            ToolCall(name="get_recent_incidents", arguments={"limit": 5}),
            ToolCall(name="get_recent_drift_events", arguments={"hostname": primary_hostname, "limit": 5}),
        ]
        return policy

    if query_class == "change_planning" and primary_hostname:
        policy["name"] = "change_planning_grounding"
        policy["reason"] = "Change-planning answers must ground target devices in inventory and current config evidence before planning."
        policy["required_tool_calls"] = [
            ToolCall(name="get_inventory_device", arguments={"hostname": primary_hostname}),
            ToolCall(name="get_device_running_config", arguments={"hostname": primary_hostname}),
        ]
        return policy

    if query_class == "compliance" and primary_hostname:
        if _CONFIG_FOCUS_PATTERN.search(normalized_message):
            policy["name"] = "config_snapshot_lookup"
            policy["reason"] = "Config backup questions should be grounded in configuration snapshot evidence before answering."
            policy["required_tool_calls"] = [
                ToolCall(name="get_device_running_config", arguments={"hostname": primary_hostname}),
            ]
            policy["recommended_tool_calls"] = [
                ToolCall(name="get_recent_drift_events", arguments={"hostname": primary_hostname, "limit": 5}),
            ]
            return policy

        policy["name"] = "compliance_grounding"
        policy["reason"] = "Compliance answers must check current config and drift evidence before answering."
        policy["required_tool_calls"] = [
            ToolCall(name="get_device_running_config", arguments={"hostname": primary_hostname}),
            ToolCall(name="get_recent_drift_events", arguments={"hostname": primary_hostname, "limit": 5}),
        ]
        return policy

    if query_class == "compliance" and _CONFIG_FOCUS_PATTERN.search(normalized_message):
        policy["name"] = "config_snapshot_lookup"
        policy["reason"] = "Config backup questions should be grounded in configuration snapshot evidence before answering."
        policy["required_tool_calls"] = [
            ToolCall(name="get_config_snapshot_summary", arguments={"limit": 20}),
        ]
        return policy

    return policy


def _arguments_satisfy_requirement(tool_name: str, required_args: Dict[str, Any], executed_args: Optional[Dict[str, Any]]) -> bool:
    if executed_args is None:
        return False
    if executed_args == required_args:
        return True
    if not required_args:
        return True
    if tool_name == "get_device_telemetry_snapshot":
        required_hostname = str(required_args.get("hostname") or "").lower()
        executed_hostname = str(executed_args.get("hostname") or "").lower()
        required_limit = required_args.get("limit")
        executed_limit = executed_args.get("limit")
        if required_hostname and required_hostname != executed_hostname:
            return False
        if isinstance(required_limit, int) and isinstance(executed_limit, int):
            return executed_limit > 0 and executed_limit <= required_limit
    return False


def policy_status(policy: Dict[str, Any], tool_args: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    required_calls: List[ToolCall] = list(policy.get("required_tool_calls") or [])
    executed_required: List[str] = []
    missing_required: List[str] = []

    for call in required_calls:
        executed_args = tool_args.get(call.name)
        if _arguments_satisfy_requirement(call.name, call.arguments, executed_args):
            executed_required.append(call.name)
        else:
            missing_required.append(call.name)

    return {
        "name": policy.get("name", "default_grounding"),
        "reason": policy.get("reason", ""),
        "required_tools": [call.name for call in required_calls],
        "recommended_tools": [call.name for call in policy.get("recommended_tool_calls", [])],
        "executed_required_tools": executed_required,
        "missing_required_tools": missing_required,
        "satisfied": not missing_required,
    }
