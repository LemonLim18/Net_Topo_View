from __future__ import annotations

import importlib.util
from typing import Any, Dict, Sequence


MILESTONE2_BASELINE_VERSION = "phase0_step2_v1"


def _module_exists(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _has_tools(tool_names: Sequence[str], *required: str) -> bool:
    available = {str(name) for name in tool_names or []}
    return all(tool in available for tool in required)


def _capability(
    milestone_step: str,
    name: str,
    status: str,
    summary: str,
    evidence: Sequence[str],
    reuse_for_m3: bool,
    next_gap: str = "",
) -> Dict[str, Any]:
    return {
        "milestone_step": milestone_step,
        "name": name,
        "status": status,
        "summary": summary,
        "evidence": list(evidence),
        "reuse_for_m3": reuse_for_m3,
        "next_gap": next_gap,
    }


def build_milestone2_baseline(tool_names: Sequence[str]) -> Dict[str, Any]:
    tool_names = list(tool_names or [])

    capabilities = [
        _capability(
            milestone_step="1-3",
            name="authoritative_context_contract",
            status="implemented" if _module_exists("llm.context_contract") else "not_implemented",
            summary="Authoritative domains, freshness metadata, and query taxonomy are defined in code.",
            evidence=["llm.context_contract"],
            reuse_for_m3=True,
            next_gap="Keep this contract canonical and avoid re-describing it in Milestone 3 except where enforcement changes.",
        ),
        _capability(
            milestone_step="4-5",
            name="per_turn_context_and_versioning",
            status="implemented" if _module_exists("llm.context_state") and _module_exists("llm.agent") else "partial",
            summary="The agent rebuilds context per turn using context versions and cache invalidation rather than relying only on a static prompt.",
            evidence=["llm.agent", "llm.context", "llm.context_state"],
            reuse_for_m3=True,
            next_gap="Milestone 3 should reuse this as a substrate and add stricter release gates on top of it.",
        ),
        _capability(
            milestone_step="6-8",
            name="inventory_and_refresh_tools",
            status=(
                "implemented"
                if _has_tools(
                    tool_names,
                    "list_inventory_devices",
                    "get_inventory_device",
                    "search_inventory_devices",
                    "get_planned_topology",
                    "compare_planned_vs_live",
                    "get_context_health",
                    "get_last_sync_status",
                    "trigger_context_refresh",
                )
                else "partial"
            ),
            summary="Planned-state inventory tools and explicit freshness or refresh tools are present in the tool surface.",
            evidence=[
                "tools:list_inventory_devices,get_inventory_device,search_inventory_devices,get_planned_topology,compare_planned_vs_live",
                "tools:get_context_health,get_last_sync_status,trigger_context_refresh",
            ],
            reuse_for_m3=True,
            next_gap="Milestone 3 should use these tools as hard evidence gates instead of redefining them.",
        ),
        _capability(
            milestone_step="9-11",
            name="source_of_truth_context_bundle",
            status="implemented" if _module_exists("rag.context_bundle") else "not_implemented",
            summary="The context bundle fuses planned inventory, planned topology, telemetry, incidents, drift, deployment, and knowledge with NetBox-first semantics and adaptive telemetry policies.",
            evidence=["rag.context_bundle", "test_context_bundle_domains", "test_context_contract"],
            reuse_for_m3=True,
            next_gap="Milestone 3 should not rebuild the bundle; it should consume and enforce it.",
        ),
        _capability(
            milestone_step="12-14",
            name="refresh_fabric_and_durable_event_log",
            status=(
                "implemented"
                if _module_exists("llm.refresh_fabric") and _module_exists("db.manager")
                else "partial"
            ),
            summary="Event-driven invalidation, targeted refresh jobs, listeners, and a durable context event log are present in code.",
            evidence=["llm.refresh_fabric", "llm.refresh_state", "db.manager:append_context_event_log/get_context_event_log", "backend.py:register_refresh_sink"],
            reuse_for_m3=True,
            next_gap="Milestone 3 should build debugging and correction loops on top of these events instead of introducing a second event model.",
        ),
        _capability(
            milestone_step="15-18",
            name="partitioned_retrieval_and_delta_embeddings",
            status=(
                "implemented"
                if _module_exists("llm.retriever") and _module_exists("rag.auto_embedder")
                else "partial"
            ),
            summary="Retrieval planning uses query classes and partitions, and the auto-embedder supports partitioned and delta-oriented state updates.",
            evidence=["llm.retriever", "rag.auto_embedder", "llm.vector_store"],
            reuse_for_m3=True,
            next_gap="Milestone 3 should trust these retrieval partitions and add stricter action-time validation instead of replacing routing again.",
        ),
        _capability(
            milestone_step="19",
            name="knowledge_curation",
            status="partial" if _module_exists("rag.knowledge_curation") else "not_implemented",
            summary="Knowledge curation support exists, but corpus quality and breadth remain operational rather than architectural work.",
            evidence=["rag.knowledge_curation"],
            reuse_for_m3=False,
            next_gap="Continue content curation separately; Milestone 3 should avoid claiming knowledge completeness.",
        ),
        _capability(
            milestone_step="20-22",
            name="mcp_resources_and_semantics_map",
            status="partial" if _module_exists("mcp.resources") else "not_implemented",
            summary="MCP resources expose inventory, topology, telemetry, config snapshots, incidents, change jobs, context health, and canonical semantics, but parity and completeness still need proving.",
            evidence=["mcp.resources", "mcp.tools", "llm.api:/semantics,/canonical-device-state"],
            reuse_for_m3=True,
            next_gap="Milestone 3 should align decisions and diagnostics across MCP, REST, and prompt context rather than redefine semantics.",
        ),
        _capability(
            milestone_step="23-25",
            name="grounded_answers_conflicts_and_policy_gates",
            status=(
                "implemented"
                if _module_exists("llm.answer_grounding") and _module_exists("llm.answer_policy")
                else "partial"
            ),
            summary="The backend already tracks source, scope, confidence, conflicts, and required-tool policies for major query classes.",
            evidence=["llm.answer_grounding", "llm.answer_policy", "test_answer_grounding", "test_agent_routing_policy"],
            reuse_for_m3=True,
            next_gap="Milestone 3 should harden release-time enforcement and uncertainty wording, not recreate provenance logic.",
        ),
        _capability(
            milestone_step="26-28",
            name="observability_benchmarks_and_resilience",
            status=(
                "implemented"
                if _module_exists("llm.benchmark_suite") and _module_exists("llm.resilience_suite") and _module_exists("dashboard.api")
                else "partial"
            ),
            summary="The repo already has benchmark, resilience, and dashboard-level observability surfaces for context health and retrieval telemetry.",
            evidence=["llm.benchmark_suite", "llm.resilience_suite", "dashboard.api", "rag.api:state_embedding_status"],
            reuse_for_m3=True,
            next_gap="Milestone 3 should extend these suites toward action safety, policy bypass, and rollback validation.",
        ),
        _capability(
            milestone_step="29-30",
            name="advanced_digital_twin_and_change_learning",
            status="partial" if _module_exists("rag.digital_twin") and _module_exists("llm.change_intelligence") else "not_implemented",
            summary="Digital twin and change-intelligence foundations exist, but they are differentiators beyond the minimum Milestone 2 substrate.",
            evidence=["rag.digital_twin", "llm.change_intelligence"],
            reuse_for_m3=False,
            next_gap="Treat these as later-stage differentiators, not as a prerequisite for Phase 0 and Phase 1 enforcement work.",
        ),
    ]

    counts = {
        "implemented": sum(1 for item in capabilities if item["status"] == "implemented"),
        "partial": sum(1 for item in capabilities if item["status"] == "partial"),
        "not_implemented": sum(1 for item in capabilities if item["status"] == "not_implemented"),
    }

    reusable = [item["name"] for item in capabilities if item["reuse_for_m3"]]
    redundancy_risk = [
        item["name"]
        for item in capabilities
        if item["reuse_for_m3"] and item["status"] == "implemented"
    ]

    return {
        "version": MILESTONE2_BASELINE_VERSION,
        "summary": (
            "Most of the Milestone 2 grounding substrate already exists in code. Milestone 3 should primarily enforce and operationalize it, "
            "not re-plan freshness, routing, retrieval, MCP semantics, or provenance from scratch."
        ),
        "counts": counts,
        "capabilities": capabilities,
        "reusable_foundations": reusable,
        "redundancy_risk_for_m3": redundancy_risk,
    }


def render_milestone2_baseline_prompt(tool_names: Sequence[str]) -> str:
    baseline = build_milestone2_baseline(tool_names)
    lines = []
    for item in baseline["capabilities"]:
        if item["status"] == "implemented" and item["reuse_for_m3"]:
            lines.append(f"- {item['name']}: implemented and reusable")
        elif item["status"] == "partial":
            lines.append(f"- {item['name']}: partial; do not overclaim")
    return (
        "## Milestone 2 Foundation Baseline\n"
        f"Version: {baseline['version']}\n"
        f"Summary: {baseline['summary']}\n"
        "Use existing implemented foundations where available, and explicitly mark partial areas as limited instead of implying they are complete.\n"
        + "\n".join(lines)
    )