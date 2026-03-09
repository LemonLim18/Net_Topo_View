from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from time import perf_counter
from typing import Any, Dict, List, Optional, Sequence

from llm.context_contract import classify_query


BENCHMARK_SUITE_VERSION = "phase8_step26_v1"
_PASSING_CASE_SCORE = 0.85
_PASSING_METRIC_SCORE = 0.60


BENCHMARK_CASES: List[Dict[str, Any]] = [
    {
        "id": "inventory_management_ip",
        "category": "inventory_only",
        "prompt": "What is the management IP of SW1 in inventory?",
        "expected_query_class": "inventory_only",
        "expected_answer_scope": "planned",
        "expected_response_types": ["text"],
        "expected_policy_name": "management_ip_lookup",
        "expected_policy_required_tools": ["get_inventory_device"],
        "required_source_groups": [["netbox", "local_db"]],
        "freshness_expectation": "fresh_or_warm",
        "require_conflicts": False,
    },
    {
        "id": "live_topology_status",
        "category": "live_only",
        "prompt": "Show me the live topology right now.",
        "expected_query_class": "live_only",
        "expected_answer_scope": "live",
        "expected_response_types": ["text"],
        "expected_policy_name": "live_state_lookup",
        "expected_policy_required_tools": ["get_topology_summary"],
        "required_source_groups": [["crawler_cache", "sdn_controller"]],
        "freshness_expectation": "fresh_or_warm",
        "require_conflicts": False,
    },
    {
        "id": "planned_live_conflict",
        "category": "mixed_state",
        "prompt": "Compare planned vs live state for SW1.",
        "expected_query_class": "planned_vs_live",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "expected_policy_name": "planned_vs_live_compare",
        "expected_policy_required_tools": ["compare_planned_vs_live"],
        "required_source_groups": [["netbox", "local_db"], ["crawler_cache", "sdn_controller"]],
        "freshness_expectation": "fresh_or_warm",
        "require_conflicts": True,
    },
    {
        "id": "stale_telemetry_signal",
        "category": "stale_data",
        "prompt": "Is telemetry stale for SW1 right now?",
        "expected_query_class": "live_only",
        "expected_answer_scope": "live",
        "expected_response_types": ["text"],
        "expected_policy_name": "live_state_lookup",
        "expected_policy_required_tools": ["get_device_details"],
        "required_source_groups": [["influxdb", "prometheus"]],
        "freshness_expectation": "stale_signal",
        "require_conflicts": True,
    },
    {
        "id": "incident_troubleshooting",
        "category": "incident",
        "prompt": "Troubleshoot why SW1 is down right now.",
        "expected_query_class": "troubleshooting",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "expected_policy_name": "troubleshooting_multisource",
        "expected_policy_required_tools": [
            "get_inventory_device",
            "get_device_details",
            "get_device_telemetry_snapshot",
            "get_device_running_config",
        ],
        "required_source_groups": [["netbox", "local_db"], ["crawler_cache", "sdn_controller"], ["incident_store"]],
        "freshness_expectation": "fresh_or_warm",
        "require_conflicts": True,
    },
    {
        "id": "change_plan_vlan",
        "category": "change_planning",
        "prompt": "Plan change for VLAN 20 on SW1.",
        "expected_query_class": "change_planning",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["proposal", "intent_plan"],
        "expected_policy_name": "change_planning_grounding",
        "expected_policy_required_tools": ["get_inventory_device", "get_device_running_config"],
        "required_source_groups": [["netbox", "local_db"], ["config_snapshots", "running_config_cache"]],
        "freshness_expectation": "fresh_or_warm",
        "require_conflicts": False,
    },
]


def list_benchmark_cases(selected_categories: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    if not selected_categories:
        return [dict(case) for case in BENCHMARK_CASES]

    requested = {str(category).strip().lower() for category in selected_categories if str(category).strip()}
    available = {str(case["category"]).lower() for case in BENCHMARK_CASES}
    unknown = sorted(requested - available)
    if unknown:
        raise ValueError(f"Unknown benchmark categories: {', '.join(unknown)}")
    return [dict(case) for case in BENCHMARK_CASES if str(case["category"]).lower() in requested]


def benchmark_categories() -> List[str]:
    seen = []
    for case in BENCHMARK_CASES:
        category = str(case["category"])
        if category not in seen:
            seen.append(category)
    return seen


def _rounded(score: float) -> float:
    return round(max(0.0, min(score, 1.0)), 3)


def _metric(score: float, reasons: List[str]) -> Dict[str, Any]:
    rounded = _rounded(score)
    return {
        "score": rounded,
        "passed": rounded >= _PASSING_METRIC_SCORE,
        "reasons": reasons,
    }


def _source_selection_metric(case: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    grounding_meta = response.get("grounding_meta") or {}
    source_used = {str(source) for source in grounding_meta.get("source_used") or []}
    answer_policy = grounding_meta.get("answer_policy") or {}
    reasons: List[str] = []
    matched = 0
    checks = 0

    for group in case.get("required_source_groups", []):
        checks += 1
        if any(source in source_used for source in group):
            matched += 1
        else:
            reasons.append(f"missing authoritative source from {group}")

    expected_tools = set(case.get("expected_policy_required_tools") or [])
    if expected_tools:
        checks += 1
        executed = set(answer_policy.get("executed_required_tools") or [])
        required = set(answer_policy.get("required_tools") or [])
        if expected_tools.issubset(required) and expected_tools.issubset(executed) and answer_policy.get("satisfied"):
            matched += 1
        else:
            reasons.append("required answer-policy tools were not fully satisfied")

    if checks == 0:
        score = 1.0 if source_used else 0.0
    else:
        score = matched / checks
    return _metric(score, reasons)


def _freshness_metric(case: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    grounding_meta = response.get("grounding_meta") or {}
    source_age = list(grounding_meta.get("source_age") or [])
    conflicts = list(grounding_meta.get("conflicts") or [])
    reasons: List[str] = []
    expectation = case.get("freshness_expectation", "any")

    freshness_classes = [str(entry.get("freshness_class") or "unknown") for entry in source_age]
    if expectation == "any":
        score = 1.0 if source_age else 0.0
        if not source_age:
            reasons.append("missing source freshness metadata")
        return _metric(score, reasons)

    if expectation == "fresh_or_warm":
        if not freshness_classes:
            reasons.append("missing source freshness metadata")
            return _metric(0.0, reasons)
        good = sum(1 for freshness_class in freshness_classes if freshness_class in {"live", "warm"})
        if good != len(freshness_classes):
            reasons.append("one or more grounding sources are stale or missing")
        return _metric(good / len(freshness_classes), reasons)

    stale_conflict_codes = {
        "telemetry_stale",
        "telemetry_missing",
        "config_snapshot_stale",
        "config_snapshot_missing",
    }
    stale_signal_present = any(freshness_class in {"stale", "missing"} for freshness_class in freshness_classes)
    if not stale_signal_present:
        stale_signal_present = any(str(conflict.get("code")) in stale_conflict_codes for conflict in conflicts)
    if not stale_signal_present:
        reasons.append("benchmark expected stale-data signaling but none was surfaced")
    return _metric(1.0 if stale_signal_present else 0.0, reasons)


def _conflict_metric(case: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    grounding_meta = response.get("grounding_meta") or {}
    conflicts = list(grounding_meta.get("conflicts") or [])
    content = str(response.get("content") or "")
    require_conflicts = bool(case.get("require_conflicts"))
    reasons: List[str] = []

    if require_conflicts:
        if conflicts and "Conflicts:" in content:
            return _metric(1.0, reasons)
        if conflicts:
            reasons.append("conflicts were detected internally but not surfaced in the answer")
            return _metric(0.5, reasons)
        reasons.append("benchmark expected a surfaced conflict but none was present")
        return _metric(0.0, reasons)

    if conflicts:
        reasons.append("unexpected conflict surfaced for a clean benchmark case")
        return _metric(0.0 if "Conflicts:" in content else 0.25, reasons)
    return _metric(1.0, reasons)


def _factuality_metric(case: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    grounding_meta = response.get("grounding_meta") or {}
    answer_policy = grounding_meta.get("answer_policy") or {}
    reasons: List[str] = []
    matched = 0
    checks = 0

    classified_query_class = classify_query(str(case.get("prompt") or "")).query_class
    expected_query_class = case.get("expected_query_class")
    checks += 1
    if classified_query_class == expected_query_class:
        matched += 1
    else:
        reasons.append(
            f"prompt classified as {classified_query_class}, expected {expected_query_class}"
        )

    checks += 1
    if str(grounding_meta.get("answer_scope") or "") == str(case.get("expected_answer_scope") or ""):
        matched += 1
    else:
        reasons.append("answer scope did not match the benchmark expectation")

    checks += 1
    if str(response.get("type") or "text") in set(case.get("expected_response_types") or ["text"]):
        matched += 1
    else:
        reasons.append("response type did not match the benchmark expectation")

    checks += 1
    if "Grounding:" in str(response.get("content") or ""):
        matched += 1
    else:
        reasons.append("grounding footer missing from answer content")

    expected_policy_name = str(case.get("expected_policy_name") or "")
    if expected_policy_name:
        checks += 1
        if str(answer_policy.get("name") or "") == expected_policy_name and bool(answer_policy.get("satisfied")):
            matched += 1
        else:
            reasons.append("answer policy did not match or was not satisfied")

    return _metric(matched / checks if checks else 0.0, reasons)


def evaluate_benchmark_response(
    case: Dict[str, Any],
    response: Dict[str, Any],
    latency_seconds: float,
) -> Dict[str, Any]:
    metrics = {
        "factuality": _factuality_metric(case, response),
        "freshness": _freshness_metric(case, response),
        "source_selection": _source_selection_metric(case, response),
        "conflict_handling": _conflict_metric(case, response),
    }
    weighted_score = (
        metrics["factuality"]["score"] * 0.35
        + metrics["freshness"]["score"] * 0.25
        + metrics["source_selection"]["score"] * 0.25
        + metrics["conflict_handling"]["score"] * 0.15
    )
    overall_score = _rounded(weighted_score)
    passed = overall_score >= _PASSING_CASE_SCORE and all(metric["passed"] for metric in metrics.values())
    failure_reasons: List[str] = []
    for metric_name, metric in metrics.items():
        if not metric["passed"]:
            failure_reasons.extend([f"{metric_name}: {reason}" for reason in metric["reasons"]])

    grounding_meta = response.get("grounding_meta") or {}
    return {
        "id": case["id"],
        "category": case["category"],
        "prompt": case["prompt"],
        "query_class": classify_query(case["prompt"]).query_class,
        "expected_query_class": case["expected_query_class"],
        "response_type": str(response.get("type") or "text"),
        "answer_scope": grounding_meta.get("answer_scope"),
        "source_used": list(grounding_meta.get("source_used") or []),
        "conflict_count": len(list(grounding_meta.get("conflicts") or [])),
        "latency_ms": round(latency_seconds * 1000, 2),
        "overall_score": overall_score,
        "passed": passed,
        "metrics": metrics,
        "failure_reasons": failure_reasons,
    }


def summarize_benchmark_results(case_results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    overall_scores = [float(result["overall_score"]) for result in case_results]
    category_summary: Dict[str, Dict[str, Any]] = {}
    for result in case_results:
        category = str(result["category"])
        bucket = category_summary.setdefault(
            category,
            {"total": 0, "passed": 0, "failed": 0, "average_score": 0.0, "average_latency_ms": 0.0},
        )
        bucket["total"] += 1
        if result["passed"]:
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1

    for category, bucket in category_summary.items():
        category_results = [result for result in case_results if result["category"] == category]
        bucket["average_score"] = _rounded(mean(float(result["overall_score"]) for result in category_results))
        bucket["average_latency_ms"] = round(mean(float(result["latency_ms"]) for result in category_results), 2)

    metric_summary: Dict[str, float] = {}
    if case_results:
        for metric_name in ("factuality", "freshness", "source_selection", "conflict_handling"):
            metric_summary[metric_name] = _rounded(
                mean(float(result["metrics"][metric_name]["score"]) for result in case_results)
            )

    failed_cases = [result["id"] for result in case_results if not result["passed"]]
    return {
        "total_cases": len(case_results),
        "passed_cases": len(case_results) - len(failed_cases),
        "failed_cases": len(failed_cases),
        "overall_score": _rounded(mean(overall_scores)) if overall_scores else 0.0,
        "category_summary": category_summary,
        "metric_summary": metric_summary,
        "failed_case_ids": failed_cases,
        "regression_guard": {
            "passed": not failed_cases,
            "minimum_case_score": _PASSING_CASE_SCORE,
            "minimum_metric_score": _PASSING_METRIC_SCORE,
            "failed_case_ids": failed_cases,
        },
    }


async def run_benchmark_suite(agent, selected_categories: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    selected_cases = list_benchmark_cases(selected_categories)
    case_results: List[Dict[str, Any]] = []

    for case in selected_cases:
        if hasattr(agent, "clear_history"):
            agent.clear_history()
        started = perf_counter()
        response = await agent.chat(case["prompt"])
        elapsed = perf_counter() - started
        case_results.append(evaluate_benchmark_response(case, response, elapsed))

    summary = summarize_benchmark_results(case_results)
    model_name = getattr(agent, "model_name", "unknown")
    if callable(model_name):
        model_name = model_name()

    return {
        "suite_version": BENCHMARK_SUITE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "categories": benchmark_categories(),
        "scoring_method": "deterministic evidence-backed scoring using grounding, freshness, source selection, and conflict metadata",
        **summary,
        "cases": case_results,
    }