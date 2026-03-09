import asyncio

from llm import api as llm_api


class _FakeAgent:
    model_name = "test-model"


def test_get_canonical_semantics_endpoint(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())
    monkeypatch.setattr(
        llm_api.network_tools,
        "get_canonical_semantics_map",
        lambda: '{"version":"phase6_step22_v1","device_presence_states":{"planned_and_live":"ok"}}',
    )

    payload = asyncio.run(llm_api.get_canonical_semantics())

    assert payload["version"] == "phase6_step22_v1"
    assert "device_presence_states" in payload


def test_get_canonical_device_state_endpoint_filters_hostname(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())
    monkeypatch.setattr(
        llm_api.network_tools,
        "compare_planned_vs_live",
        lambda: (
            '{"devices":[{"hostname":"R1","presence_state":"planned_and_live"},'
            '{"hostname":"SW1","presence_state":"planned_only"}],'
            '"matching_devices":["R1"],"planned_only":["SW1"],"live_only":[],"semantics_map":{"version":"phase6_step22_v1"}}'
        ),
    )

    payload = asyncio.run(llm_api.get_canonical_device_state(hostname="sw1"))

    assert len(payload["devices"]) == 1
    assert payload["devices"][0]["hostname"] == "SW1"
    assert payload["planned_only"] == ["SW1"]


def test_chat_endpoint_returns_grounding_meta(monkeypatch):
    class _FakeAgent:
        model_name = "test-model"

        async def chat(self, message):
            return {
                "role": "assistant",
                "content": "answer\n\nGrounding: planned; sources=local_db; source_age=local_db:0s/live; confidence=high (0.9).",
                "type": "text",
                "grounding_meta": {
                    "answer_scope": "planned",
                    "source_used": ["local_db"],
                    "source_age": [{"source": "local_db", "scope": "planned_inventory", "age_seconds": 0, "freshness_class": "live", "confidence": 0.9}],
                    "confidence": {"level": "high", "score": 0.9, "reason": "test"},
                },
            }

    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())

    payload = asyncio.run(llm_api.chat(llm_api.ChatRequest(message="What is SW1?")))

    assert payload.grounding_meta["answer_scope"] == "planned"
    assert payload.grounding_meta["source_used"] == ["local_db"]


def test_benchmark_cases_endpoint_returns_step26_categories(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())

    payload = asyncio.run(llm_api.get_benchmark_cases())

    assert "inventory_only" in payload["categories"]
    assert "stale_data" in payload["categories"]
    assert any(case["id"] == "incident_troubleshooting" for case in payload["cases"])


def test_run_chat_benchmark_endpoint_returns_structured_report(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())
    monkeypatch.setattr(
        llm_api,
        "run_benchmark_suite",
        lambda agent, selected_categories=None: asyncio.sleep(0, result={
            "suite_version": "phase8_step26_v1",
            "total_cases": 1,
            "passed_cases": 1,
            "failed_cases": 0,
            "overall_score": 1.0,
            "regression_guard": {"passed": True, "failed_case_ids": []},
            "cases": [{"id": "inventory_management_ip", "passed": True}],
        }),
    )

    payload = asyncio.run(
        llm_api.run_chat_benchmark(llm_api.BenchmarkRunRequest(categories=["inventory_only"]))
    )

    assert payload["suite_version"] == "phase8_step26_v1"
    assert payload["regression_guard"]["passed"] is True
    assert payload["cases"][0]["id"] == "inventory_management_ip"


def test_resilience_cases_endpoint_returns_step28_scenarios():
    payload = asyncio.run(llm_api.get_resilience_cases())

    assert "partial_outage" in payload["categories"]
    assert "event_storm" in payload["categories"]
    assert any(scenario["id"] == "crawl_failed_incidents_continue" for scenario in payload["scenarios"])


def test_run_chat_resilience_endpoint_returns_structured_report(monkeypatch):
    monkeypatch.setattr(
        llm_api,
        "run_resilience_suite",
        lambda selected_categories=None: {
            "suite_version": "phase8_step28_v1",
            "total_scenarios": 1,
            "passed_scenarios": 1,
            "failed_scenarios": 0,
            "overall_score": 1.0,
            "regression_guard": {"passed": True, "failed_scenario_ids": []},
            "scenarios": [{"id": "netbox_ok_telemetry_stale", "passed": True}],
        },
    )

    payload = asyncio.run(
        llm_api.run_chat_resilience(llm_api.ResilienceRunRequest(categories=["partial_outage"]))
    )

    assert payload["suite_version"] == "phase8_step28_v1"
    assert payload["regression_guard"]["passed"] is True
    assert payload["scenarios"][0]["id"] == "netbox_ok_telemetry_stale"


def test_change_intelligence_info_endpoint_returns_step29_version():
    payload = asyncio.run(llm_api.get_change_intelligence_info())

    assert payload["analysis_version"] == "phase9_step29_v1"


def test_run_change_intelligence_endpoint_returns_structured_report(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())
    monkeypatch.setattr(
        llm_api.network_tools,
        "analyze_change_intelligence",
        lambda intent_json, user_role="operator": (
            '{"analysis_version":"phase9_step29_v1","approval_recommendation":"manual_review",'
            '"cross_domain_risk":{"level":"high","score":68},'
            '"what_if_simulation":{"decision":"manual_review"}}'
        ),
    )

    payload = asyncio.run(
        llm_api.run_change_intelligence(
            llm_api.ChangeIntelligenceRequest(intent_json='{"id":"intent-1"}', user_role="operator")
        )
    )

    assert payload["analysis_version"] == "phase9_step29_v1"
    assert payload["approval_recommendation"] == "manual_review"
    assert payload["cross_domain_risk"]["level"] == "high"