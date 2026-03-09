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
                "content": "Prepared a VLAN change plan for SW1.\n\nGrounding: mixed; sources=local_db,config_snapshots; source_age=local_db:0s/live,config_snapshots:90s/warm; confidence=high (0.9).",
                "type": "intent_plan",
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


def test_run_benchmark_suite_scores_all_step26_categories():
    agent = _BenchmarkFakeAgent()

    report = asyncio.run(run_benchmark_suite(agent))

    assert report["suite_version"] == BENCHMARK_SUITE_VERSION
    assert report["total_cases"] == 6
    assert report["passed_cases"] == 6
    assert report["regression_guard"]["passed"] is True
    assert agent.clear_count == 6
    assert report["metric_summary"]["factuality"] == 1.0
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