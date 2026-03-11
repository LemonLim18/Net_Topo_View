import asyncio

from llm.benchmark_suite import BENCHMARK_SUITE_VERSION, evaluate_benchmark_response, run_benchmark_suite


class _BenchmarkFakeAgent:
    def __init__(self):
        self.model_name = "benchmark-fake"
        self.clear_count = 0

    def clear_history(self):
        self.clear_count += 1

    async def chat(self, message):
        responses = {
            "What is the management IP of SW1 in inventory?": {
                "role": "assistant",
                "content": "SW1 management IP is 10.0.0.2.\n\nGrounding: planned; sources=local_db; source_age=local_db:0s/live; confidence=high (0.9).",
                "type": "text",
                "grounding_meta": {
                    "answer_scope": "planned",
                    "source_used": ["local_db"],
                    "source_age": [{"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9}],
                    "conflicts": [],
                    "answer_policy": {
                        "name": "management_ip_lookup",
                        "required_tools": ["get_inventory_device"],
                        "executed_required_tools": ["get_inventory_device"],
                        "satisfied": True,
                    },
                },
            },
            "Compare planned vs live state for SW1.": {
                "role": "assistant",
                "content": "Planned and live state disagree for SW1.\n\nGrounding: mixed; sources=local_db,crawler_cache; source_age=local_db:0s/live,crawler_cache:12s/live; confidence=high (0.91).\nConflicts: SW1 has different management IP values in planned and live state.",
                "type": "text",
                "grounding_meta": {
                    "answer_scope": "mixed",
                    "source_used": ["local_db", "crawler_cache"],
                    "source_age": [
                        {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.88},
                        {"source": "crawler_cache", "scope": "live_topology", "age_seconds": 12, "freshness_class": "live", "confidence": 0.91},
                    ],
                    "conflicts": [{"code": "management_ip_mismatch", "summary": "SW1 mismatch"}],
                    "answer_policy": {
                        "name": "planned_vs_live_compare",
                        "required_tools": ["compare_planned_vs_live"],
                        "executed_required_tools": ["compare_planned_vs_live"],
                        "satisfied": True,
                    },
                },
            },
            "Troubleshoot SW1 health while InfluxDB is stale and crawl data is fresh.": {
                "role": "assistant",
                "content": "Limited answer: SW1 still looks reachable from planned inventory and fresh crawl data, but InfluxDB telemetry is stale even after a refresh attempt, so current health should be treated as degraded.\n\nGrounding: mixed; sources=local_db,crawler_cache,influxdb; source_age=local_db:0s/live,crawler_cache:10s/live,influxdb:1900s/stale; confidence=medium (0.72).\nConflicts: Telemetry freshness is stale for SW1.",
                "type": "text",
                "grounding_meta": {
                    "answer_scope": "mixed",
                    "source_used": ["local_db", "crawler_cache", "influxdb"],
                    "source_age": [
                        {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.89},
                        {"source": "crawler_cache", "scope": "live_topology", "age_seconds": 10, "freshness_class": "live", "confidence": 0.91},
                        {"source": "influxdb", "scope": "telemetry", "age_seconds": 1900, "freshness_class": "stale", "confidence": 0.55},
                    ],
                    "conflicts": [{"code": "telemetry_stale", "summary": "telemetry stale"}],
                    "answer_policy": {
                        "name": "troubleshooting_multisource",
                        "required_tools": ["get_inventory_device", "get_device_details", "get_device_telemetry_snapshot"],
                        "executed_required_tools": ["get_inventory_device", "get_device_details", "get_device_telemetry_snapshot"],
                        "satisfied": True,
                    },
                    "answer_release": {
                        "decision": "allow_limited",
                        "refresh_attempted": True,
                        "degraded_signals": ["stale_live_evidence"],
                    },
                    "self_correction": {
                        "attempts": [
                            {"tool_name": "get_device_telemetry_snapshot", "failure_kind": "missing_telemetry", "attempt": 1, "outcome": "retry_failed"}
                        ],
                        "escalations": [],
                    },
                },
            },
            "Plan change for MACsec on Huawei VRP for SW1.": {
                "role": "assistant",
                "content": "I cannot create a change proposal because MACsec is unsupported for this Huawei VRP workflow and cannot be approved safely.\n\nGrounding: mixed; sources=local_db,config_snapshots,knowledge_base; source_age=local_db:0s/live,config_snapshots:80s/warm,knowledge_base:0s/live; confidence=high (0.88).",
                "type": "text",
                "grounding_meta": {
                    "answer_scope": "mixed",
                    "source_used": ["local_db", "config_snapshots", "knowledge_base"],
                    "source_age": [
                        {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                        {"source": "config_snapshots", "scope": "config_history", "age_seconds": 80, "freshness_class": "warm", "confidence": 0.84},
                        {"source": "knowledge_base", "scope": "documentation", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                    ],
                    "conflicts": [],
                    "answer_policy": {
                        "name": "change_planning_grounding",
                        "required_tools": ["get_inventory_device", "get_device_running_config"],
                        "executed_required_tools": ["get_inventory_device", "get_device_running_config"],
                        "satisfied": True,
                    },
                },
            },
            "Plan change for VLAN 30 on SW1 and R1.": {
                "role": "assistant",
                "content": "Prepared a multi-device VLAN plan for SW1 and R1 pending approval.\n\nGrounding: mixed; sources=local_db,config_snapshots; source_age=local_db:0s/live,config_snapshots:95s/warm; confidence=high (0.9).",
                "type": "intent_plan",
                "proposal": {
                    "action_artifact": {
                        "approval": {"status": "pending"},
                        "execution": {"primary_engine": "ansible_native"},
                        "scope": {"device_count": 2},
                    }
                },
                "grounding_meta": {
                    "answer_scope": "mixed",
                    "source_used": ["local_db", "config_snapshots"],
                    "source_age": [
                        {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                        {"source": "config_snapshots", "scope": "config_history", "age_seconds": 95, "freshness_class": "warm", "confidence": 0.87},
                    ],
                    "conflicts": [],
                    "answer_policy": {
                        "name": "change_planning_grounding",
                        "required_tools": ["get_inventory_device", "get_device_running_config"],
                        "executed_required_tools": ["get_inventory_device", "get_device_running_config"],
                        "satisfied": True,
                    },
                },
            },
            "Plan change to recover a malformed playbook for SW1 interface change.": {
                "role": "assistant",
                "content": "The malformed playbook was refined using the validation errors and remains pending approval after validation passed.\n\nGrounding: mixed; sources=local_db,config_snapshots; source_age=local_db:0s/live,config_snapshots:110s/warm; confidence=high (0.87).",
                "type": "intent_plan",
                "proposal": {
                    "action_artifact": {
                        "approval": {"status": "pending"},
                        "execution": {"primary_engine": "ansible_native"},
                    }
                },
                "refinement": {
                    "attempts_used": 2,
                    "validation": {"passed": True},
                },
                "grounding_meta": {
                    "answer_scope": "mixed",
                    "source_used": ["local_db", "config_snapshots"],
                    "source_age": [
                        {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.89},
                        {"source": "config_snapshots", "scope": "config_history", "age_seconds": 110, "freshness_class": "warm", "confidence": 0.84},
                    ],
                    "conflicts": [],
                    "answer_policy": {
                        "name": "change_planning_grounding",
                        "required_tools": ["get_inventory_device", "get_device_running_config"],
                        "executed_required_tools": ["get_inventory_device", "get_device_running_config"],
                        "satisfied": True,
                    },
                },
            },
            "Troubleshoot why the last deployment on SW1 failed verification.": {
                "role": "assistant",
                "content": "Post-change verification failed for SW1, so the result should stay under review and rollback should be explicitly reviewed before any further action.\n\nGrounding: mixed; sources=deployment_repository,config_snapshots,influxdb; source_age=deployment_repository:15s/live,config_snapshots:120s/warm,influxdb:25s/live; confidence=high (0.86).\nConflicts: Verification failed after deployment and requires rollback review.",
                "type": "text",
                "recommended_action": {"action": "rollback_recommended", "reason": "verification failed"},
                "post_change_verification": {
                    "outcome": "failed",
                    "checks": {"telemetry_outcome": {"status": "fail"}},
                },
                "grounding_meta": {
                    "answer_scope": "mixed",
                    "source_used": ["deployment_repository", "config_snapshots", "influxdb"],
                    "source_age": [
                        {"source": "deployment_repository", "scope": "deployment_status", "age_seconds": 15, "freshness_class": "live", "confidence": 0.93},
                        {"source": "config_snapshots", "scope": "config_history", "age_seconds": 120, "freshness_class": "warm", "confidence": 0.83},
                        {"source": "influxdb", "scope": "telemetry", "age_seconds": 25, "freshness_class": "live", "confidence": 0.88},
                    ],
                    "conflicts": [{"code": "verification_failed", "summary": "verification failed"}],
                    "answer_policy": {
                        "name": "troubleshooting_multisource",
                        "required_tools": ["get_inventory_device", "get_device_details", "get_device_running_config"],
                        "executed_required_tools": ["get_inventory_device", "get_device_details", "get_device_running_config"],
                        "satisfied": True,
                    },
                },
            },
            "What is the management IP of SW1 while NetBox is unreachable?": {
                "role": "assistant",
                "content": "Limited answer: SW1 management IP appears to be 10.0.0.2 from the local inventory cache, but NetBox is unavailable so the authoritative planned source could not be refreshed.\n\nGrounding: planned; sources=local_db,netbox; source_age=local_db:0s/live,netbox:unknown/missing; confidence=medium (0.68).\nConflicts: Planned inventory authority is degraded because NetBox is unavailable.",
                "type": "text",
                "grounding_meta": {
                    "answer_scope": "planned",
                    "source_used": ["local_db", "netbox"],
                    "source_age": [
                        {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.89},
                        {"source": "netbox", "scope": "planned_inventory", "age_seconds": None, "freshness_class": "missing", "confidence": 0.0},
                    ],
                    "conflicts": [{"code": "planned_inventory_unavailable", "summary": "NetBox unavailable"}],
                    "answer_policy": {
                        "name": "management_ip_lookup",
                        "required_tools": ["get_inventory_device"],
                        "executed_required_tools": ["get_inventory_device"],
                        "satisfied": True,
                    },
                    "answer_release": {
                        "decision": "allow_limited",
                        "refresh_attempted": False,
                        "degraded_signals": ["source_unavailable"],
                    },
                    "self_correction": {
                        "attempts": [
                            {"tool_name": "get_inventory_device", "failure_kind": "source_unavailable", "attempt": 1, "outcome": "retry_failed"}
                        ],
                        "escalations": [],
                    },
                },
            },
            "Show me the live topology right now.": {
                "role": "assistant",
                "content": "Live topology shows R1 connected to SW1.\n\nGrounding: live; sources=crawler_cache; source_age=crawler_cache:8s/live; confidence=high (0.92).",
                "type": "text",
                "grounding_meta": {
                    "answer_scope": "live",
                    "source_used": ["crawler_cache"],
                    "source_age": [{"source": "crawler_cache", "scope": "live_topology", "age_seconds": 8, "freshness_class": "live", "confidence": 0.92}],
                    "conflicts": [],
                    "answer_policy": {
                        "name": "live_state_lookup",
                        "required_tools": ["get_topology_summary"],
                        "executed_required_tools": ["get_topology_summary"],
                        "satisfied": True,
                    },
                },
            },
            "Troubleshoot why SW1 is down right now.": {
                "role": "assistant",
                "content": "SW1 is down with an active incident and conflicting planned versus live state.\n\nGrounding: mixed; sources=local_db,crawler_cache,incident_store,config_snapshots; source_age=local_db:0s/live,crawler_cache:10s/live,incident_store:20s/live,config_snapshots:120s/warm; confidence=high (0.89).\nConflicts: SW1 is active in planned inventory but currently down in live topology.",
                "type": "text",
                "grounding_meta": {
                    "answer_scope": "mixed",
                    "source_used": ["local_db", "crawler_cache", "incident_store", "config_snapshots"],
                    "source_age": [
                        {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.88},
                        {"source": "crawler_cache", "scope": "live_topology", "age_seconds": 10, "freshness_class": "live", "confidence": 0.92},
                        {"source": "incident_store", "scope": "incident_state", "age_seconds": 20, "freshness_class": "live", "confidence": 0.9},
                        {"source": "config_snapshots", "scope": "config_history", "age_seconds": 120, "freshness_class": "warm", "confidence": 0.84},
                    ],
                    "conflicts": [{"code": "operational_state_conflict", "summary": "SW1 down"}],
                    "answer_policy": {
                        "name": "troubleshooting_multisource",
                        "required_tools": [
                            "get_inventory_device",
                            "get_device_details",
                            "get_device_telemetry_snapshot",
                            "get_device_running_config",
                        ],
                        "executed_required_tools": [
                            "get_inventory_device",
                            "get_device_details",
                            "get_device_telemetry_snapshot",
                            "get_device_running_config",
                        ],
                        "satisfied": True,
                    },
                },
            },
            "Plan change for VLAN 20 on SW1.": {
                "role": "assistant",
                "content": "Prepared a VLAN change plan for SW1 pending approval.\n\nGrounding: mixed; sources=local_db,config_snapshots; source_age=local_db:0s/live,config_snapshots:90s/warm; confidence=high (0.9).",
                "type": "intent_plan",
                "proposal": {
                    "action_artifact": {
                        "approval": {"status": "pending"},
                        "execution": {"primary_engine": "ansible_native"},
                    }
                },
                "grounding_meta": {
                    "answer_scope": "mixed",
                    "source_used": ["local_db", "config_snapshots"],
                    "source_age": [
                        {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                        {"source": "config_snapshots", "scope": "config_history", "age_seconds": 90, "freshness_class": "warm", "confidence": 0.88},
                    ],
                    "conflicts": [],
                    "answer_policy": {
                        "name": "change_planning_grounding",
                        "required_tools": ["get_inventory_device", "get_device_running_config"],
                        "executed_required_tools": ["get_inventory_device", "get_device_running_config"],
                        "satisfied": True,
                    },
                },
            },
        }
        return responses[message]


def test_run_benchmark_suite_scores_all_step18_categories():
    agent = _BenchmarkFakeAgent()

    report = asyncio.run(run_benchmark_suite(agent))

    assert report["suite_version"] == BENCHMARK_SUITE_VERSION
    assert report["total_cases"] == 11
    assert report["passed_cases"] == 11
    assert report["regression_guard"]["passed"] is True
    assert agent.clear_count == 11
    assert report["metric_summary"]["task_completion"] == 1.0
    assert report["metric_summary"]["honesty"] == 1.0
    assert report["metric_summary"]["retry_behavior"] == 1.0
    assert report["metric_summary"]["approval_safety"] == 1.0
    assert report["metric_summary"]["debugging_usefulness"] == 1.0
    assert report["category_summary"]["partial_dependency_outage"]["passed"] == 1


def test_evaluate_benchmark_response_flags_missing_conflict_footer():
    case = {
        "id": "planned_live_conflict",
        "category": "mixed_state",
        "prompt": "Compare planned vs live state for SW1.",
        "expected_query_class": "planned_vs_live",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "expected_policy_name": "planned_vs_live_compare",
        "expected_policy_required_tools": ["compare_planned_vs_live"],
        "required_source_groups": [["local_db"], ["crawler_cache"]],
        "freshness_expectation": "fresh_or_warm",
        "require_conflicts": True,
    }
    response = {
        "type": "text",
        "content": "Mismatch detected.\n\nGrounding: mixed; sources=local_db,crawler_cache; source_age=local_db:0s/live,crawler_cache:10s/live; confidence=high (0.9).",
        "grounding_meta": {
            "answer_scope": "mixed",
            "source_used": ["local_db", "crawler_cache"],
            "source_age": [
                {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                {"source": "crawler_cache", "scope": "live_topology", "age_seconds": 10, "freshness_class": "live", "confidence": 0.92},
            ],
            "conflicts": [{"code": "management_ip_mismatch", "summary": "SW1 mismatch"}],
            "answer_policy": {
                "name": "planned_vs_live_compare",
                "required_tools": ["compare_planned_vs_live"],
                "executed_required_tools": ["compare_planned_vs_live"],
                "satisfied": True,
            },
        },
    }

    result = evaluate_benchmark_response(case, response, latency_seconds=0.01)

    assert result["passed"] is False
    assert result["metrics"]["conflict_handling"]["score"] == 0.5
    assert any("conflict_handling:" in reason for reason in result["failure_reasons"])


def test_evaluate_benchmark_response_flags_missing_honesty_language_for_degraded_source():
    case = {
        "id": "netbox_unreachable_inventory_lookup",
        "category": "partial_dependency_outage",
        "prompt": "What is the management IP of SW1 while NetBox is unreachable?",
        "expected_query_class": "inventory_only",
        "expected_answer_scope": "planned",
        "expected_response_types": ["text"],
        "expected_policy_name": "management_ip_lookup",
        "expected_policy_required_tools": ["get_inventory_device"],
        "required_source_groups": [["local_db"], ["netbox"]],
        "freshness_expectation": "stale_signal",
        "require_conflicts": True,
        "degraded_sources": [{"source": "netbox", "freshness_class": "missing"}],
        "require_limited_answer": True,
    }
    response = {
        "type": "text",
        "content": "SW1 management IP is 10.0.0.2.\n\nGrounding: planned; sources=local_db,netbox; source_age=local_db:0s/live,netbox:unknown/missing; confidence=medium (0.7).\nConflicts: NetBox unavailable.",
        "grounding_meta": {
            "answer_scope": "planned",
            "source_used": ["local_db", "netbox"],
            "source_age": [
                {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                {"source": "netbox", "scope": "planned_inventory", "age_seconds": None, "freshness_class": "missing", "confidence": 0.0},
            ],
            "conflicts": [{"code": "planned_inventory_unavailable", "summary": "NetBox unavailable"}],
            "answer_policy": {
                "name": "management_ip_lookup",
                "required_tools": ["get_inventory_device"],
                "executed_required_tools": ["get_inventory_device"],
                "satisfied": True,
            },
        },
    }

    result = evaluate_benchmark_response(case, response, latency_seconds=0.01)

    assert result["passed"] is False
    assert result["metrics"]["honesty"]["score"] == 0.0
    assert any("honesty:" in reason for reason in result["failure_reasons"])


def test_evaluate_benchmark_response_flags_missing_retry_evidence_for_stale_outage():
    case = {
        "id": "telemetry_stale_troubleshooting",
        "category": "stale_data",
        "prompt": "Troubleshoot SW1 health while InfluxDB is stale and crawl data is fresh.",
        "expected_query_class": "troubleshooting",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "expected_policy_name": "troubleshooting_multisource",
        "expected_policy_required_tools": ["get_inventory_device", "get_device_details", "get_device_telemetry_snapshot"],
        "required_source_groups": [["local_db"], ["crawler_cache"], ["influxdb"]],
        "freshness_expectation": "stale_signal",
        "require_conflicts": True,
        "degraded_sources": [{"source": "influxdb", "freshness_class": "stale"}],
        "required_retry_paths": [["grounding_meta", "answer_release", "refresh_attempted"], ["grounding_meta", "self_correction", "attempts"]],
        "retry_attempt_path": ["grounding_meta", "self_correction", "attempts"],
        "minimum_retry_attempts": 1,
        "require_limited_answer": True,
    }
    response = {
        "type": "text",
        "content": "telemetry is stale.\n\nNote: Some required evidence is stale or missing.\n\nGrounding: mixed; sources=local_db,crawler_cache,influxdb; source_age=local_db:0s/live,crawler_cache:10s/live,influxdb:1900s/stale; confidence=medium (0.7).\nConflicts: Telemetry freshness is stale for SW1.",
        "grounding_meta": {
            "answer_scope": "mixed",
            "source_used": ["local_db", "crawler_cache", "influxdb"],
            "source_age": [
                {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                {"source": "crawler_cache", "scope": "live_topology", "age_seconds": 10, "freshness_class": "live", "confidence": 0.9},
                {"source": "influxdb", "scope": "telemetry", "age_seconds": 1900, "freshness_class": "stale", "confidence": 0.55},
            ],
            "conflicts": [{"code": "telemetry_stale", "summary": "telemetry stale"}],
            "answer_policy": {
                "name": "troubleshooting_multisource",
                "required_tools": ["get_inventory_device", "get_device_details", "get_device_telemetry_snapshot"],
                "executed_required_tools": ["get_inventory_device", "get_device_details", "get_device_telemetry_snapshot"],
                "satisfied": True,
            },
            "answer_release": {"decision": "allow_limited", "refresh_attempted": False, "degraded_signals": ["stale_live_evidence"]},
            "self_correction": {"attempts": [], "escalations": []},
        },
    }

    result = evaluate_benchmark_response(case, response, latency_seconds=0.01)

    assert result["passed"] is False
    assert result["metrics"]["retry_behavior"]["score"] < 1.0
    assert any("retry_behavior:" in reason for reason in result["failure_reasons"])
