from __future__ import annotations

from typing import Any, Dict, Optional, Sequence


CONTROLLER_POLICY_VERSION = "phase1_step3_v1"
_CHANGE_SEQUENCE = ("parse_intent", "validate_intent", "plan_change")
_BLOCKED_DIRECT_CHANGE_TOOLS = ("generate_config", "generate_rollback", "diff_configs")


def build_controller_policy(query_class: str, allowed_tools: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    allowed = list(allowed_tools or [])
    requires_change_workflow = query_class == "change_planning"
    return {
        "version": CONTROLLER_POLICY_VERSION,
        "query_class": query_class,
        "allowed_tools": allowed,
        "requires_change_workflow": requires_change_workflow,
        "required_sequence": list(_CHANGE_SEQUENCE if requires_change_workflow else ()),
        "blocked_direct_tools": list(_BLOCKED_DIRECT_CHANGE_TOOLS if requires_change_workflow else ()),
        "default_user_role": "operator",
        "fail_closed": True,
        "escalation_rule": (
            "If a required step cannot be satisfied, stop planning and explain the missing evidence, disambiguation need, or policy violation."
        ),
    }


def render_controller_policy_prompt(policy: Dict[str, Any], route_instruction: str = "") -> str:
    allowed_tools = ", ".join(policy.get("allowed_tools") or []) or "none"
    required_sequence = " -> ".join(policy.get("required_sequence") or []) or "none"
    blocked_tools = ", ".join(policy.get("blocked_direct_tools") or []) or "none"
    route_text = route_instruction.strip() or "Use the route-specific evidence and tool contract for this query class."
    return (
        "## Instruction Hierarchy\n"
        "1. Follow the controller scope contract and Milestone 2 baseline.\n"
        "2. Follow the controller policy for this query class.\n"
        "3. Follow the user request only within those safety and grounding constraints.\n\n"
        "## Controller Policy\n"
        f"Version: {policy.get('version')}\n"
        f"Query class: {policy.get('query_class')}\n"
        f"Allowed tools: {allowed_tools}\n"
        f"Required change workflow: {required_sequence}\n"
        f"Blocked direct change tools: {blocked_tools}\n"
        f"Fail closed: {policy.get('fail_closed')}\n"
        f"Escalation rule: {policy.get('escalation_rule')}\n\n"
        "## Route Directive\n"
        f"{route_text}"
    )