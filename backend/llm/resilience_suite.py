from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence

from llm.context_contract import classify_query


RESILIENCE_SUITE_VERSION = "phase8_step28_v1"
_PASSING_CASE_SCORE = 0.85
_PASSING_METRIC_SCORE = 0.60
_HONESTY_HINTS = ("stale", "missing", "unavailable", "lag", "degraded", "fallback", "queued")


RESILIENCE_SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "netbox_ok_telemetry_stale",
        "category": "partial_outage",
        "prompt": "Is SW1 healthy right now while telemetry is stale?",
        "expected_query_class": "live_only",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "required_source_groups": [["netbox", "local_db"], ["crawler_cache"], ["influxdb", "prometheus"]],
        "degraded_sources": [{"source": "influxdb", "freshness_class": "stale"}],
        "require_conflicts": True,
        "expected_event_domains": ["telemetry"],
        "response_fixture": {
            "role": "assistant",
            "content": "SW1 still appears reachable from planned inventory and the latest crawl, but telemetry is stale so current health is degraded and should not be treated as fully current.\n\nGrounding: mixed; sources=local_db,crawler_cache,influxdb; source_age=local_db:0s/live,crawler_cache:18s/live,influxdb:1900s/stale; confidence=medium (0.73).\nConflicts: Telemetry freshness is stale for SW1.",
            "type": "text",
            "grounding_meta": {
                "answer_scope": "mixed",
                "source_used": ["local_db", "crawler_cache", "influxdb"],
                "source_age": [
                    {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                    {"source": "crawler_cache", "scope": "live_topology", "age_seconds": 18, "freshness_class": "live", "confidence": 0.9},
                    {"source": "influxdb", "scope": "telemetry", "age_seconds": 1900, "freshness_class": "stale", "confidence": 0.55},
                ],
                "conflicts": [{"code": "telemetry_stale", "summary": "Telemetry freshness is stale for SW1."}],
            },
        },
        "context_health": {
            "event_lag": {"latest_event_at": "2026-03-09T10:25:00+00:00", "recent_event_count": 1},
            "missing_mappings": {"count": 0},
        },
        "replayed_events": [
            {"source": "monitor.threshold_engine", "action": "telemetry_freshness_breach", "domains": ["telemetry"]},
        ],
    },
    {
        "id": "crawl_failed_incidents_continue",
        "category": "partial_outage",
        "prompt": "Troubleshoot SW1 while the latest crawl is failing but incidents are still arriving.",
        "expected_query_class": "troubleshooting",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "required_source_groups": [["incident_store"], ["local_db"], ["crawler_cache"]],
        "degraded_sources": [{"source": "crawler_cache", "freshness_class": "missing"}],
        "require_conflicts": True,
        "expected_event_domains": ["live_topology", "incidents"],
        "response_fixture": {
            "role": "assistant",
            "content": "SW1 has an active incident, but the latest crawl data is missing, so live adjacency and current device state cannot be confirmed from fresh topology.\n\nGrounding: mixed; sources=local_db,crawler_cache,incident_store; source_age=local_db:0s/live,crawler_cache:unknown/missing,incident_store:22s/live; confidence=medium (0.68).\nConflicts: SW1 has an active incident while live topology evidence is missing or stale.",
            "type": "text",
            "grounding_meta": {
                "answer_scope": "mixed",
                "source_used": ["local_db", "crawler_cache", "incident_store"],
                "source_age": [
                    {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.88},
                    {"source": "crawler_cache", "scope": "live_topology", "age_seconds": None, "freshness_class": "missing", "confidence": 0.0},
                    {"source": "incident_store", "scope": "incident_state", "age_seconds": 22, "freshness_class": "live", "confidence": 0.91},
                ],
                "conflicts": [{"code": "active_incident", "summary": "SW1 has an active incident while crawl evidence is unavailable."}],
            },
        },
        "context_health": {
            "event_lag": {"latest_event_at": "2026-03-09T10:26:00+00:00", "recent_event_count": 2},
            "missing_mappings": {"count": 0},
        },
        "replayed_events": [
            {"source": "crawler.store", "action": "crawl_failed", "domains": ["live_topology"]},
            {"source": "incident_store", "action": "incident_state_changed", "domains": ["incidents"]},
        ],
    },
    {
        "id": "deployment_inventory_sync_lag",
        "category": "partial_outage",
        "prompt": "Compare planned vs live state for SW1 after the deployment completed but inventory sync is queued.",
        "expected_query_class": "planned_vs_live",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "required_source_groups": [["deployment_repository"], ["local_db"], ["crawler_cache"]],
        "degraded_sources": [{"source": "netbox", "freshness_class": "warm"}],
        "require_conflicts": True,
        "expected_event_domains": ["deployment_status", "planned_inventory"],
        "response_fixture": {
            "role": "assistant",
            "content": "The deployment appears to have changed SW1 faster than the planned inventory sync has caught up, and NetBox is still lagging with a queued sync, so planned and live state should be treated as temporarily divergent.\n\nGrounding: mixed; sources=deployment_repository,local_db,crawler_cache,netbox; source_age=deployment_repository:14s/live,local_db:0s/live,crawler_cache:12s/live,netbox:420s/warm; confidence=high (0.82).\nConflicts: SW1 has different management IP values in planned and live state during sync lag.",
            "type": "text",
            "grounding_meta": {
                "answer_scope": "mixed",
                "source_used": ["deployment_repository", "local_db", "crawler_cache", "netbox"],
                "source_age": [
                    {"source": "deployment_repository", "scope": "deployment_status", "age_seconds": 14, "freshness_class": "live", "confidence": 0.93},
                    {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.87},
                    {"source": "crawler_cache", "scope": "live_topology", "age_seconds": 12, "freshness_class": "live", "confidence": 0.92},
                    {"source": "netbox", "scope": "planned_inventory", "age_seconds": 420, "freshness_class": "warm", "confidence": 0.7},
                ],
                "conflicts": [{"code": "management_ip_mismatch", "summary": "SW1 differs between planned and live during sync lag."}],
            },
        },
        "context_health": {
            "event_lag": {"latest_event_at": "2026-03-09T10:27:00+00:00", "recent_event_count": 2},
            "missing_mappings": {"count": 1},
        },
        "replayed_events": [
            {"source": "deployment_pipeline", "action": "deployment_job_completed", "domains": ["deployment_status"]},
            {"source": "netbox.sync_devices", "action": "inventory_sync_queued", "domains": ["planned_inventory"]},
        ],
    },
    {
        "id": "event_storm_replay",
        "category": "event_storm",
        "prompt": "Troubleshoot the event storm around SW1 right now.",
        "expected_query_class": "troubleshooting",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "required_source_groups": [["incident_store"], ["drift_store"], ["config_snapshots"], ["crawler_cache"], ["local_db"]],
        "degraded_sources": [],
        "require_conflicts": True,
        "expected_event_domains": ["incidents", "drift", "config_history", "planned_inventory", "live_topology"],
        "response_fixture": {
            "role": "assistant",
            "content": "SW1 is in a rapid event storm with concurrent incident, drift, config, inventory, and live-state changes; the answer remains coherent because the events converge on the same device and all conflicts are surfaced explicitly.\n\nGrounding: mixed; sources=local_db,crawler_cache,incident_store,drift_store,config_snapshots; source_age=local_db:0s/live,crawler_cache:9s/live,incident_store:11s/live,drift_store:15s/live,config_snapshots:70s/warm; confidence=high (0.88).\nConflicts: SW1 has active incident and drift signals during the event storm.",
            "type": "text",
            "grounding_meta": {
                "answer_scope": "mixed",
                "source_used": ["local_db", "crawler_cache", "incident_store", "drift_store", "config_snapshots"],
                "source_age": [
                    {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                    {"source": "crawler_cache", "scope": "live_topology", "age_seconds": 9, "freshness_class": "live", "confidence": 0.91},
                    {"source": "incident_store", "scope": "incident_state", "age_seconds": 11, "freshness_class": "live", "confidence": 0.92},
                    {"source": "drift_store", "scope": "drift_state", "age_seconds": 15, "freshness_class": "live", "confidence": 0.9},
                    {"source": "config_snapshots", "scope": "config_history", "age_seconds": 70, "freshness_class": "warm", "confidence": 0.84},
                ],
                "conflicts": [
                    {"code": "active_incident", "summary": "incident active"},
                    {"code": "config_drift_detected", "summary": "drift detected"},
                ],
            },
        },
        "context_health": {
            "event_lag": {"latest_event_at": "2026-03-09T10:28:00+00:00", "recent_event_count": 5},
            "missing_mappings": {"count": 0},
        },
        "replayed_events": [
            {"source": "incident_store", "action": "incident_state_changed", "domains": ["incidents"]},
            {"source": "drift_store", "action": "drift_detected", "domains": ["drift"]},
            {"source": "db.manager", "action": "config_snapshot_created", "domains": ["config_history"]},
            {"source": "netbox.api", "action": "inventory_device_updated", "domains": ["planned_inventory"]},
            {"source": "crawler.store", "action": "topology_state_changed", "domains": ["live_topology"]},
        ],
    },
]


def resilience_categories() -> List[str]:
    seen: List[str] = []
    for scenario in RESILIENCE_SCENARIOS:
        category = str(scenario["category"])
        if category not in seen:
            seen.append(category)
    return seen


def list_resilience_scenarios(selected_categories: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    if not selected_categories:
        return [dict(scenario) for scenario in RESILIENCE_SCENARIOS]
    requested = {str(category).strip().lower() for category in selected_categories if str(category).strip()}
    available = {str(scenario["category"]).lower() for scenario in RESILIENCE_SCENARIOS}
    unknown = sorted(requested - available)
    if unknown:
        raise ValueError(f"Unknown resilience categories: {', '.join(unknown)}")
    return [dict(scenario) for scenario in RESILIENCE_SCENARIOS if str(scenario["category"]).lower() in requested]


def _rounded(score: float) -> float:
    return round(max(0.0, min(score, 1.0)), 3)


def _metric(score: float, reasons: List[str]) -> Dict[str, Any]:
    rounded = _rounded(score)
    return {"score": rounded, "passed": rounded >= _PASSING_METRIC_SCORE, "reasons": reasons}


def _response_fixture(scenario: Dict[str, Any]) -> Dict[str, Any]:
    return dict(scenario.get("response_fixture") or {})


def _source_continuity_metric(scenario: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    source_used = {str(source) for source in (response.get("grounding_meta") or {}).get("source_used") or []}
    reasons: List[str] = []
    groups = list(scenario.get("required_source_groups") or [])
    if not groups:
        return _metric(1.0 if source_used else 0.0, ["missing grounding sources"] if not source_used else [])
    matched = 0
    for group in groups:
        if any(source in source_used for source in group):
            matched += 1
        else:
            reasons.append(f"missing surviving source from {group}")
    return _metric(matched / len(groups), reasons)


def _degraded_honesty_metric(scenario: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    grounding_meta = response.get("grounding_meta") or {}
    source_age = list(grounding_meta.get("source_age") or [])
    content = str(response.get("content") or "").lower()
    answer_body = content.split("\n\ngrounding:", 1)[0]
    degraded_sources = list(scenario.get("degraded_sources") or [])
    reasons: List[str] = []
    if not degraded_sources:
        return _metric(1.0, reasons)

    matched = 0
    for degraded in degraded_sources:
        source_name = str(degraded.get("source") or "")
        freshness_class = str(degraded.get("freshness_class") or "")
        entry = next((item for item in source_age if str(item.get("source") or "") == source_name), None)
        if entry is None:
            reasons.append(f"missing degraded source metadata for {source_name}")
            continue
        if str(entry.get("freshness_class") or "") != freshness_class:
            reasons.append(f"source {source_name} freshness was not reported as {freshness_class}")
            continue
        if not any(hint in answer_body for hint in _HONESTY_HINTS):
            reasons.append(f"answer did not explicitly acknowledge degraded source {source_name}")
            continue
        matched += 1
    return _metric(matched / len(degraded_sources), reasons)


def _replay_consistency_metric(scenario: Dict[str, Any]) -> Dict[str, Any]:
    replayed_events = list(scenario.get("replayed_events") or [])
    context_health = scenario.get("context_health") or {}
    expected_domains = set(str(domain) for domain in scenario.get("expected_event_domains") or [])
    reasons: List[str] = []
    if not replayed_events:
        return _metric(0.0, ["scenario missing replayed events"])

    domains_seen = {str(domain) for event in replayed_events for domain in event.get("domains") or []}
    matched = 0
    checks = 0

    if expected_domains:
        checks += 1
        if expected_domains.issubset(domains_seen):
            matched += 1
        else:
            reasons.append("replayed events did not cover all expected domains")

    event_lag = context_health.get("event_lag") or {}
    checks += 1
    if event_lag.get("latest_event_at") and int(event_lag.get("recent_event_count") or 0) >= len(replayed_events):
        matched += 1
    else:
        reasons.append("context health did not reflect the replayed event burst")

    if checks == 0:
        return _metric(1.0, reasons)
    return _metric(matched / checks, reasons)


def _conflict_awareness_metric(scenario: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    conflicts = list((response.get("grounding_meta") or {}).get("conflicts") or [])
    content = str(response.get("content") or "")
    require_conflicts = bool(scenario.get("require_conflicts"))
    reasons: List[str] = []
    if require_conflicts:
        if conflicts and "Conflicts:" in content:
            return _metric(1.0, reasons)
        if conflicts:
            reasons.append("conflicts were detected but not surfaced in answer text")
            return _metric(0.5, reasons)
        return _metric(0.0, ["scenario expected surfaced conflicts"])
    if conflicts:
        return _metric(0.0, ["unexpected conflicts surfaced for scenario"])
    return _metric(1.0, reasons)


def _coherence_metric(scenario: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    grounding_meta = response.get("grounding_meta") or {}
    reasons: List[str] = []
    matched = 0
    checks = 0

    checks += 1
    if classify_query(str(scenario.get("prompt") or "")).query_class == scenario.get("expected_query_class"):
        matched += 1
    else:
        reasons.append("scenario prompt no longer matches expected query class")

    checks += 1
    if str(grounding_meta.get("answer_scope") or "") == str(scenario.get("expected_answer_scope") or ""):
        matched += 1
    else:
        reasons.append("answer scope did not match scenario expectation")

    checks += 1
    if str(response.get("type") or "text") in set(scenario.get("expected_response_types") or ["text"]):
        matched += 1
    else:
        reasons.append("response type did not match scenario expectation")

    checks += 1
    if "Grounding:" in str(response.get("content") or ""):
        matched += 1
    else:
        reasons.append("grounding footer missing from resilience response")

    return _metric(matched / checks, reasons)


def evaluate_resilience_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    response = _response_fixture(scenario)
    metrics = {
        "coherence": _coherence_metric(scenario, response),
        "degraded_honesty": _degraded_honesty_metric(scenario, response),
        "source_continuity": _source_continuity_metric(scenario, response),
        "replay_consistency": _replay_consistency_metric(scenario),
        "conflict_awareness": _conflict_awareness_metric(scenario, response),
    }
    weighted_score = (
        metrics["coherence"]["score"] * 0.25
        + metrics["degraded_honesty"]["score"] * 0.25
        + metrics["source_continuity"]["score"] * 0.2
        + metrics["replay_consistency"]["score"] * 0.15
        + metrics["conflict_awareness"]["score"] * 0.15
    )
    overall_score = _rounded(weighted_score)
    passed = overall_score >= _PASSING_CASE_SCORE and all(metric["passed"] for metric in metrics.values())
    failure_reasons: List[str] = []
    for metric_name, metric in metrics.items():
        if not metric["passed"]:
            failure_reasons.extend([f"{metric_name}: {reason}" for reason in metric["reasons"]])
    return {
        "id": scenario["id"],
        "category": scenario["category"],
        "prompt": scenario["prompt"],
        "query_class": classify_query(scenario["prompt"]).query_class,
        "expected_query_class": scenario["expected_query_class"],
        "overall_score": overall_score,
        "passed": passed,
        "metrics": metrics,
        "replayed_event_count": len(list(scenario.get("replayed_events") or [])),
        "failure_reasons": failure_reasons,
    }


def summarize_resilience_results(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    overall_scores = [float(result["overall_score"]) for result in results]
    category_summary: Dict[str, Dict[str, Any]] = {}
    for result in results:
        category = str(result["category"])
        bucket = category_summary.setdefault(category, {"total": 0, "passed": 0, "failed": 0, "average_score": 0.0})
        bucket["total"] += 1
        if result["passed"]:
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1

    for category, bucket in category_summary.items():
        category_results = [result for result in results if result["category"] == category]
        bucket["average_score"] = _rounded(mean(float(result["overall_score"]) for result in category_results))

    metric_summary: Dict[str, float] = {}
    if results:
        for metric_name in ("coherence", "degraded_honesty", "source_continuity", "replay_consistency", "conflict_awareness"):
            metric_summary[metric_name] = _rounded(mean(float(result["metrics"][metric_name]["score"]) for result in results))

    failed_scenarios = [result["id"] for result in results if not result["passed"]]
    return {
        "total_scenarios": len(results),
        "passed_scenarios": len(results) - len(failed_scenarios),
        "failed_scenarios": len(failed_scenarios),
        "overall_score": _rounded(mean(overall_scores)) if overall_scores else 0.0,
        "category_summary": category_summary,
        "metric_summary": metric_summary,
        "failed_scenario_ids": failed_scenarios,
        "regression_guard": {
            "passed": not failed_scenarios,
            "minimum_case_score": _PASSING_CASE_SCORE,
            "minimum_metric_score": _PASSING_METRIC_SCORE,
            "failed_scenario_ids": failed_scenarios,
        },
    }


def run_resilience_suite(selected_categories: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    scenarios = list_resilience_scenarios(selected_categories)
    results = [evaluate_resilience_scenario(scenario) for scenario in scenarios]
    summary = summarize_resilience_results(results)
    return {
        "suite_version": RESILIENCE_SUITE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "synthetic_replay_regression",
        "categories": resilience_categories(),
        "scoring_method": "deterministic replay scoring using grounding metadata, degraded-source honesty, surviving-source continuity, conflict surfacing, and event-log consistency",
        **summary,
        "scenarios": results,
    }