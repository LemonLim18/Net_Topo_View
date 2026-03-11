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
            "Is telemetry stale for SW1 right now?": {
                "role": "assistant",
                "content": "Telemetry for SW1 is stale.\n\nGrounding: live; sources=influxdb; source_age=influxdb:1900s/stale; confidence=medium (0.6).\nConflicts: Telemetry freshness is stale for SW1.",
                "type": "text",
                "grounding_meta": {
                    "answer_scope": "live",
                    "source_used": ["influxdb"],
                    "source_age": [{"source": "influxdb", "scope": "telemetry", "age_seconds": 1900, "freshness_class": "stale", "confidence": 0.6}],
                    "conflicts": [{"code": "telemetry_stale", "summary": "telemetry stale"}],
                    "answer_policy": {
                        "name": "live_state_lookup",
                        "required_tools": ["get_device_details"],
                        "executed_required_tools": ["get_device_details"],
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
        }
        return responses[message]


def test_run_benchmark_suite_scores_all_step17_categories():
    agent = _BenchmarkFakeAgent()

    report = asyncio.run(run_benchmark_suite(agent))

    assert report["suite_version"] == BENCHMARK_SUITE_VERSION
    assert report["total_cases"] == 10
    assert report["passed_cases"] == 10
    assert report["regression_guard"]["passed"] is True
    assert agent.clear_count == 10
    assert report["metric_summary"]["factuality"] == 1.0
    assert report["metric_summary"]["approval_safety"] == 1.0
    assert report["metric_summary"]["debugging_usefulness"] == 1.0
    assert report["category_summary"]["stale_data"]["passed"] == 1


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


def test_evaluate_benchmark_response_flags_unsafe_proposal_for_unsupported_feature():
    case = {
        "id": "unsupported_vendor_feature",
        "category": "generation_safety",
        "prompt": "Plan change for MACsec on Huawei VRP for SW1.",
        "expected_query_class": "change_planning",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "expected_policy_name": "change_planning_grounding",
        "expected_policy_required_tools": ["get_inventory_device", "get_device_running_config"],
        "required_source_groups": [["local_db"], ["config_snapshots"], ["knowledge_base"]],
        "freshness_expectation": "fresh_or_warm",
        "require_conflicts": False,
        "forbid_proposal": True,
        "required_content_substrings": ["unsupported", "cannot"],
    }
    response = {
        "type": "text",
        "content": "MACsec is unsupported for this workflow and cannot be approved.\n\nGrounding: mixed; sources=local_db,config_snapshots,knowledge_base; source_age=local_db:0s/live,config_snapshots:60s/warm,knowledge_base:0s/live; confidence=high (0.9).",
        "proposal": {"action_artifact": {"approval": {"status": "pending"}}},
        "grounding_meta": {
            "answer_scope": "mixed",
            "source_used": ["local_db", "config_snapshots", "knowledge_base"],
            "source_age": [
                {"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9},
                {"source": "config_snapshots", "scope": "config_history", "age_seconds": 60, "freshness_class": "warm", "confidence": 0.85},
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
    }

    result = evaluate_benchmark_response(case, response, latency_seconds=0.01)

    assert result["passed"] is False
    assert result["metrics"]["approval_safety"]["score"] < 1.0
    assert any("approval_safety:" in reason for reason in result["failure_reasons"])


def test_evaluate_benchmark_response_flags_missing_debug_artifacts_for_verification_failure():
    case = {
        "id": "deployment_verification_failure",
        "category": "verification_failure",
        "prompt": "Troubleshoot why the last deployment on SW1 failed verification.",
        "expected_query_class": "change_planning",
        "expected_answer_scope": "mixed",
        "expected_response_types": ["text"],
        "expected_policy_name": "troubleshooting_multisource",
        "expected_policy_required_tools": ["get_inventory_device", "get_device_details", "get_device_running_config"],
        "required_source_groups": [["deployment_repository"], ["config_snapshots"], ["influxdb"]],
        "freshness_expectation": "fresh_or_warm",
        "require_conflicts": True,
        "required_recommended_action": "rollback_recommended",
        "required_content_substrings": ["verification", "rollback"],
        "required_debug_paths": [["post_change_verification", "outcome"], ["recommended_action", "action"]],
    }
    response = {
        "type": "text",
        "content": "Verification failed. Rollback review is needed.\n\nGrounding: mixed; sources=deployment_repository,config_snapshots,influxdb; source_age=deployment_repository:15s/live,config_snapshots:120s/warm,influxdb:25s/live; confidence=high (0.86).\nConflicts: Verification failed after deployment.",
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
    }

    result = evaluate_benchmark_response(case, response, latency_seconds=0.01)

    assert result["passed"] is False
    assert result["metrics"]["debugging_usefulness"]["score"] < 1.0
    assert any("debugging_usefulness:" in reason for reason in result["failure_reasons"])
