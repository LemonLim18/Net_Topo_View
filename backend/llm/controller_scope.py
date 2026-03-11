from __future__ import annotations

import importlib.util
from typing import Any, Dict, Sequence


CONTROLLER_SCOPE_VERSION = "phase0_step1_v1"


def build_controller_scope_contract() -> Dict[str, Any]:
    return {
        "version": CONTROLLER_SCOPE_VERSION,
        "objective": (
            "Operate as a grounded network action engine that answers from verified evidence, "
            "creates only validated change artifacts, and keeps all network-state mutations behind human approval."
        ),
        "mode": "human_in_the_loop",
        "chain_of_thought_policy": "private_reasoning_structured_rationale_only",
        "goals": [
            "Ground network answers in authoritative planned, live, telemetry, and knowledge evidence.",
            "Prevent speculative CLI, playbook, or deployment output from bypassing validation.",
            "Expose uncertainty, conflicts, and missing evidence honestly.",
            "Support user-expected operational workflows for query, planning, approval, deployment, and debugging.",
        ],
        "in_scope": [
            "Grounded network queries and troubleshooting.",
            "Policy-gated intent parsing and change planning.",
            "Validated vendor CLI and Ansible playbook generation.",
            "Human-approved deployment and rollback workflows.",
            "Grounding metadata, provenance, and controller observability.",
        ],
        "out_of_scope": [
            "Autonomous production changes without explicit approval.",
            "Raw user-visible chain-of-thought.",
            "Pretending unsupported or partially implemented features are fully operational.",
            "Speculative network facts without authoritative evidence.",
        ],
        "hard_rules": [
            "Never claim current network state without evidence from tools, MCP resources, or grounded context.",
            "Never generate raw CLI for direct execution outside the validated planning path.",
            "Never execute, approve, or rollback network changes directly from the chat turn.",
            "When capability status is partial or not_implemented, state the limitation and provide the safest supported path.",
        ],
    }


def _module_exists(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _capability(
    name: str,
    status: str,
    summary: str,
    evidence: Sequence[str],
    next_gap: str = "",
) -> Dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "summary": summary,
        "evidence": list(evidence),
        "next_gap": next_gap,
    }


def build_controller_implementation_status(tool_names: Sequence[str]) -> Dict[str, Any]:
    tool_set = {str(name) for name in tool_names or []}

    def has_tools(*required: str) -> bool:
        return all(tool in tool_set for tool in required)

    capabilities = [
        _capability(
            name="grounded_query_routing",
            status="implemented" if has_tools("get_context_health", "get_inventory_device", "compare_planned_vs_live") else "partial",
            summary="Authoritative query routing, evidence prefetch, and grounding metadata are available for core query classes.",
            evidence=[
                "llm.context_contract",
                "llm.answer_policy",
                "llm.answer_grounding",
                "tools:get_context_health,get_inventory_device,compare_planned_vs_live",
            ],
            next_gap="Broaden release gating so every answer class is machine-enforced, not only the current policy set.",
        ),
        _capability(
            name="human_approval_gate",
            status="implemented" if has_tools("parse_intent", "validate_intent", "plan_change") else "partial",
            summary="Config changes flow through intent parsing, policy validation, planning, and proposal approval rather than direct execution.",
            evidence=[
                "llm.agent",
                "llm.policy",
                "llm.proposal",
                "tools:parse_intent,validate_intent,plan_change",
            ],
            next_gap="Move more of the safety contract from prompt wording into explicit controller-state checks.",
        ),
        _capability(
            name="instruction_hierarchy_contract",
            status="implemented" if _module_exists("llm.controller_policy") and _module_exists("llm.system_prompt") else "partial",
            summary="Instruction hierarchy, route policy, and prompt sections are centralized and enforced through the controller loop.",
            evidence=["llm.agent", "llm.controller_policy", "llm.system_prompt", "llm.controller_scope"],
            next_gap="Extend the same hard-enforcement discipline into later playbook and post-deploy verification flows.",
        ),
        _capability(
            name="bounded_self_correction",
            status="implemented" if _module_exists("llm.controller_self_correction") else "partial",
            summary="The controller now runs bounded retry-and-observe loops for malformed arguments, failed reads, missing telemetry, and plan/schema-related repair paths, with explicit escalation once the cap is reached.",
            evidence=["llm.agent", "llm.controller_self_correction", "llm.controller_flow"],
            next_gap="Broaden repair policies as additional playbook and deployment-validation tools become controller-visible.",
        ),
        _capability(
            name="answer_release_gate",
            status="implemented" if _module_exists("llm.answer_release") else "partial",
            summary="Final answer release now evaluates grounding freshness, authority, and conflicts, then either requests refresh evidence or downgrades to an explicitly limited answer instead of fabricating confidence.",
            evidence=["llm.agent", "llm.answer_release", "llm.answer_grounding", "tools:get_last_sync_status,trigger_context_refresh"],
            next_gap="Expand the same release discipline into post-change verification and action-result reporting.",
        ),
        _capability(
            name="playbook_refinement",
            status="implemented" if _module_exists("ansible.api") and _module_exists("ansible.builder") else "not_implemented",
            summary="Playbook generation now runs through a bounded refine-and-verify loop that reuses builder validation, inventory checks, optional dry-run checks, auditable version history, and explicit escalation once the retry cap is reached.",
            evidence=["ansible.api:_build_playbook_with_refinement", "ansible.api:_ai_refine_playbook", "ansible.builder:validate_playbook"],
            next_gap="Extend the same bounded remediation pattern into post-change verification and rollback recommendation loops.",
        ),
        _capability(
            name="generation_safety",
            status="implemented" if _module_exists("config_engine.safety") else ("partial" if _module_exists("config_engine.validator") else "not_implemented"),
            summary="Template rendering, syntax validation, rollback viability, execution-engine compatibility, dangerous-command blocking, and playbook safety are enforced through a shared Step 10 validation layer.",
            evidence=["config_engine.renderer", "config_engine.validator", "config_engine.safety", "llm.api:/proposals/{id}/approve", "ansible.builder"],
            next_gap="Extend the same validation report into bounded playbook refinement and post-change verification loops.",
        ),
        _capability(
            name="rollback_planning",
            status="implemented" if _module_exists("config_engine.rollback_planner") and _module_exists("deploy.rollback") else "partial",
            summary="Rollback planning now reuses latest config snapshots and config diffs during planning, and runtime rollback prefers restoring the captured pre-change snapshot over optimistic undo commands.",
            evidence=["config_engine.rollback_planner", "llm.network_tools:plan_change", "deploy.rollback", "deploy.pipeline"],
            next_gap="Expose rollback-planning metadata through more operator-facing API and verification surfaces.",
        ),
        _capability(
            name="post_change_verification",
            status="implemented" if _module_exists("llm.post_change_verification") and _module_exists("deploy.verifier") else ("partial" if _module_exists("deploy.verifier") else "not_implemented"),
            summary="Post-deploy execution now produces a deterministic verification report spanning config presence, deployment outcome, telemetry freshness, incidents, drift, and correction or rollback guidance.",
            evidence=["llm.post_change_verification", "llm.api:/proposals/{id}/approve", "deploy.verifier:Verifier"],
            next_gap="Keep rollback execution explicitly human-approved while expanding richer verification evidence and operator tooling.",
        ),
        _capability(
            name="bounded_action_autonomy",
            status="implemented" if _module_exists("llm.post_change_verification") and _module_exists("deploy.pipeline") else "partial",
            summary="The controller may auto-retry read, validation, and refinement paths, but deployment and rollback execution remain explicit human-approved actions.",
            evidence=["llm.controller_scope", "llm.api:/proposals/{id}/approve", "llm.api:/proposals/{id}/rollback", "deploy.pipeline"],
            next_gap="Extend the same explicit autonomy boundary into every operator-facing deployment surface and audit trail.",
        ),
        _capability(
            name="advanced_differentiator_reservation",
            status="implemented" if _module_exists("llm.advanced_differentiators") else "partial",
            summary="Advanced differentiators such as digital-twin reasoning, change-outcome learning, policy simulation, and autonomous remediation are reserved behind a stability gate until Milestone 3 validation is explicitly accepted.",
            evidence=["llm.advanced_differentiators", "llm.api:/advanced-differentiators", "rag.api:/digital-twin/preview", "agent.api:/agent/plan"],
            next_gap="Unlock these surfaces only after the controller reaches operational stability and operators explicitly enable them.",
        ),
        _capability(
            name="mcp_semantic_alignment",
            status="partial" if has_tools("compare_planned_vs_live") and _module_exists("mcp.resources") else "not_implemented",
            summary="Canonical semantics mapping and MCP resources exist, but parity between MCP, REST, prompt context, and all controller decisions is not fully proven.",
            evidence=["tools:get_canonical_semantics_map", "mcp.resources", "llm.api:/semantics"],
            next_gap="Expand diagnostics and parity tests so every interface expresses the same state model.",
        ),
    ]

    counts = {
        "implemented": sum(1 for item in capabilities if item["status"] == "implemented"),
        "partial": sum(1 for item in capabilities if item["status"] == "partial"),
        "not_implemented": sum(1 for item in capabilities if item["status"] == "not_implemented"),
    }

    readiness = "grounding_ready_but_not_action_complete"
    if counts["not_implemented"] == 0 and counts["partial"] == 0:
        readiness = "operationally_complete"
    elif counts["implemented"] == 0:
        readiness = "not_ready"

    return {
        "version": CONTROLLER_SCOPE_VERSION,
        "readiness": readiness,
        "summary": (
            "The backend already supports grounded querying and approval-gated planning, but action safety, bounded self-correction, "
            "bounded execution autonomy, and advanced-differentiator reservation still require Milestone 3 hardening."
        ),
        "counts": counts,
        "capabilities": capabilities,
    }


def render_controller_scope_prompt(tool_names: Sequence[str]) -> str:
    contract = build_controller_scope_contract()
    status = build_controller_implementation_status(tool_names)
    capability_lines = "\n".join(
        f"- {item['name']}: {item['status']} — {item['summary']}"
        for item in status["capabilities"]
    )
    hard_rules = "\n".join(f"- {rule}" for rule in contract["hard_rules"])
    in_scope = "\n".join(f"- {item}" for item in contract["in_scope"])
    out_of_scope = "\n".join(f"- {item}" for item in contract["out_of_scope"])

    return (
        "## Controller Scope Contract\n"
        f"Version: {contract['version']}\n"
        f"Objective: {contract['objective']}\n"
        f"Operating mode: {contract['mode']}\n"
        f"Reasoning policy: {contract['chain_of_thought_policy']}\n\n"
        "In scope:\n"
        f"{in_scope}\n\n"
        "Out of scope:\n"
        f"{out_of_scope}\n\n"
        "Hard rules:\n"
        f"{hard_rules}\n\n"
        "Current implementation baseline:\n"
        f"{capability_lines}\n\n"
        "If a requested capability is partial or not_implemented, state the limitation and provide the safest supported next step."
    )