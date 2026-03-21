import asyncio

import pytest

from llm import api as llm_api
from llm.controller_observability import clear_latest_controller_chat_snapshot, record_controller_chat_snapshot
from llm.controller_scope import build_controller_implementation_status, build_controller_scope_contract
from llm.milestone_baseline import build_milestone2_baseline
from llm.proposal import ConfigProposal


class _FakeAgent:
    model_name = "test-model"
    provider_name = "gemini"

    @property
    def available_tools(self):
        return [
            "get_context_health",
            "get_inventory_device",
            "compare_planned_vs_live",
            "parse_intent",
            "validate_intent",
            "plan_change",
        ]

    def get_history(self):
        return []

    def clear_history(self):
        return None

    def set_provider(self, provider, reset_history=True):
        self.provider_name = "ollama" if "ollama" in provider.__class__.__name__.lower() else "gemini"
        self.model_name = provider.model_name()

    @property
    def controller_scope_contract(self):
        return build_controller_scope_contract()

    @property
    def controller_implementation_status(self):
        return build_controller_implementation_status(self.available_tools)

    @property
    def milestone2_baseline(self):
        return build_milestone2_baseline(self.available_tools)


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


def test_info_endpoint_returns_controller_scope_and_status(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())

    payload = asyncio.run(llm_api.get_info())

    assert payload.scope_contract["version"] == "phase0_step1_v1"
    assert payload.implementation_status["counts"]["implemented"] >= 1
    assert payload.milestone2_baseline["version"] == "phase0_step2_v1"
    capability_names = {item["name"] for item in payload.implementation_status["capabilities"]}
    assert "bounded_self_correction" in capability_names
    assert "advanced_differentiator_reservation" in capability_names


def test_runtime_endpoint_returns_provider_catalog(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())
    monkeypatch.setattr(
        llm_api,
        "_build_runtime_state",
        lambda agent, ollama_base_url=None: {
            "active_provider": "gemini",
            "active_model": "gemini-2.5-flash",
            "active_base_url": None,
            "scope": "shared_agent",
            "providers": [
                {"name": "gemini", "label": "Google Gemini", "available": True, "base_url": None, "models": ["gemini-2.5-flash"]},
                {"name": "ollama", "label": "Ollama VM", "available": True, "base_url": ollama_base_url, "models": ["qwen2.5:14b", "llama3.2"]},
            ],
        },
    )

    payload = asyncio.run(llm_api.get_chat_runtime(ollama_base_url="http://192.168.1.252:11434"))

    assert payload["active_provider"] == "gemini"
    assert payload["providers"][1]["base_url"] == "http://192.168.1.252:11434"
    assert "qwen2.5:14b" in payload["providers"][1]["models"]


def test_runtime_update_switches_agent_provider(monkeypatch):
    class _DummyOllamaProvider:
        def __init__(self, model: str, base_url: str):
            self._model = model
            self._base_url = base_url

        def model_name(self) -> str:
            return self._model

    class _RuntimeAgent(_FakeAgent):
        def __init__(self):
            self.model_name = "gemini-2.5-flash"
            self.provider_name = "gemini"
            self.last_reset_history = None

        def set_provider(self, provider, reset_history=True):
            self.provider_name = "ollama"
            self.model_name = provider.model_name()
            self.active_base_url = provider._base_url
            self.last_reset_history = reset_history

    agent = _RuntimeAgent()

    monkeypatch.setattr(llm_api, "_get_agent", lambda: agent)
    monkeypatch.setattr(
        llm_api,
        "_instantiate_runtime_provider",
        lambda provider_name, model, ollama_base_url: _DummyOllamaProvider(model or "qwen2.5:14b", ollama_base_url or "http://192.168.1.252:11434"),
    )
    monkeypatch.setattr(
        llm_api,
        "_build_runtime_state",
        lambda current_agent, ollama_base_url=None: {
            "active_provider": current_agent.provider_name,
            "active_model": current_agent.model_name,
            "active_base_url": getattr(current_agent, "active_base_url", None),
            "scope": "shared_agent",
            "providers": [],
        },
    )

    payload = asyncio.run(
        llm_api.update_chat_runtime(
            llm_api.ChatRuntimeUpdateRequest(
                provider="ollama",
                model="qwen2.5:14b",
                ollama_base_url="http://192.168.1.252:11434",
                clear_history=True,
            )
        )
    )

    assert agent.provider_name == "ollama"
    assert agent.model_name == "qwen2.5:14b"
    assert agent.last_reset_history is True
    assert payload["active_base_url"] == "http://192.168.1.252:11434"


def test_observability_endpoint_returns_controller_snapshot(monkeypatch):
    class _FakeProposalManager:
        def list_all(self):
            return [
                ConfigProposal(
                    id="prop-1",
                    status="pending",
                    target_device="R1",
                    feature="ospf",
                    risk_level="medium",
                    action_artifact={
                        "action_type": "single_device_cli",
                        "validation": {"passed": True, "errors": [], "warnings": [], "version": "phase3_step10_v1"},
                        "approval": {"status": "pending", "safety_validated": True, "safety_errors": []},
                    },
                )
            ]

    class _ObservabilityAgent(_FakeAgent):
        @property
        def proposal_manager(self):
            return _FakeProposalManager()

    clear_latest_controller_chat_snapshot()
    record_controller_chat_snapshot(
        "What changed on R1?",
        {
            "type": "text",
            "grounding_meta": {"answer_scope": "live", "source_used": ["crawler_cache"]},
        },
        model="test-model",
    )

    monkeypatch.setattr(llm_api, "_get_agent", lambda: _ObservabilityAgent())
    monkeypatch.setattr(llm_api.network_tools, "get_context_health", lambda: '{"versions":{"live_topology":5}}')
    monkeypatch.setattr(llm_api.network_tools, "get_last_sync_status", lambda: '{"operations":{"inventory":{"status":"success"}}}')
    monkeypatch.setattr(
        llm_api,
        "_build_config_snapshots_payload",
        lambda agent: {"devices_with_snapshots": 1, "devices_missing_snapshots": [], "snapshots": []},
    )
    monkeypatch.setattr(
        llm_api,
        "_build_deployment_state_payload",
        lambda agent: {"jobs": [{"id": 21, "status": "running"}], "intents": [], "change_requests": []},
    )

    payload = asyncio.run(llm_api.get_controller_observability())

    assert payload["version"] == "phase4_step14_v1"
    assert payload["controller"]["model"] == "test-model"
    assert payload["context_health"]["versions"]["live_topology"] == 5
    assert payload["sync_freshness"]["operations"]["inventory"]["status"] == "success"
    assert payload["proposal_state"]["total"] == 1
    assert payload["validation_outcomes"]["passed"] == 1
    assert payload["deployment_state"]["jobs"][0]["id"] == 21
    assert payload["grounding_metadata"]["latest"]["grounding_meta"]["answer_scope"] == "live"


def test_scope_endpoint_returns_contract_and_baseline(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())

    payload = asyncio.run(llm_api.get_controller_scope())

    assert payload["scope_contract"]["mode"] == "human_in_the_loop"
    assert payload["implementation_status"]["readiness"] == "grounding_ready_but_not_action_complete"
    assert payload["milestone2_baseline"]["counts"]["implemented"] >= 1
    capability_names = {item["name"] for item in payload["implementation_status"]["capabilities"]}
    assert "bounded_action_autonomy" in capability_names
    assert "advanced_differentiator_reservation" in capability_names


def test_advanced_differentiators_endpoint_reports_reserved_status(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())

    payload = asyncio.run(llm_api.get_advanced_differentiators())

    assert payload["version"] == "phase7_step19_v1"
    assert payload["mode"] == "reserved_until_stable"
    assert payload["availability"] == "reserved"
    assert payload["features"]["digital_twin"]["availability"] == "reserved"


def test_milestone2_baseline_endpoint_returns_foundation_report(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())

    payload = asyncio.run(llm_api.get_milestone2_baseline())

    assert payload["version"] == "phase0_step2_v1"
    reusable = set(payload["reusable_foundations"])
    assert "authoritative_context_contract" in reusable
    assert "grounded_answers_conflicts_and_policy_gates" in reusable


def test_benchmark_cases_endpoint_returns_step26_categories(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())

    payload = asyncio.run(llm_api.get_benchmark_cases())

    assert "inventory_only" in payload["categories"]
    assert "stale_data" in payload["categories"]
    assert "generation_safety" in payload["categories"]
    assert "verification_failure" in payload["categories"]
    assert "partial_dependency_outage" in payload["categories"]
    assert any(case["id"] == "incident_troubleshooting" for case in payload["cases"])
    assert any(case["id"] == "deployment_verification_failure" for case in payload["cases"])
    assert any(case["id"] == "netbox_unreachable_inventory_lookup" for case in payload["cases"])


def test_run_chat_benchmark_endpoint_returns_structured_report(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())
    monkeypatch.setattr(
        llm_api,
        "run_benchmark_suite",
        lambda agent, selected_categories=None: asyncio.sleep(0, result={
            "suite_version": "phase6_step18_v1",
            "total_cases": 1,
            "passed_cases": 1,
            "failed_cases": 0,
            "overall_score": 1.0,
            "metric_summary": {"honesty": 1.0, "retry_behavior": 1.0, "approval_safety": 1.0, "debugging_usefulness": 1.0},
            "regression_guard": {"passed": True, "failed_case_ids": []},
            "cases": [{"id": "inventory_management_ip", "passed": True}],
        }),
    )

    payload = asyncio.run(
        llm_api.run_chat_benchmark(llm_api.BenchmarkRunRequest(categories=["inventory_only"]))
    )

    assert payload["suite_version"] == "phase6_step18_v1"
    assert payload["regression_guard"]["passed"] is True
    assert payload["metric_summary"]["honesty"] == 1.0
    assert payload["metric_summary"]["retry_behavior"] == 1.0
    assert payload["metric_summary"]["approval_safety"] == 1.0
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


def test_change_intelligence_info_endpoint_returns_step29_version(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())
    payload = asyncio.run(llm_api.get_change_intelligence_info())

    assert payload["analysis_version"] == "phase9_step29_v1"
    assert payload["stability_gate"]["availability"] == "reserved"
    assert payload["stability_gate"]["feature"]["name"] == "change_intelligence"


def test_run_change_intelligence_endpoint_returns_structured_report(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())
    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            llm_api.run_change_intelligence(
                llm_api.ChangeIntelligenceRequest(intent_json='{"id":"intent-1"}', user_role="operator")
            )
        )

    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.detail["stability_gate"]["feature"]["name"] == "change_intelligence"


def test_digital_twin_info_endpoint_returns_step30_version(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())
    payload = asyncio.run(llm_api.get_digital_twin_info())

    assert payload["version"] == "phase9_step30_v1"
    assert payload["stability_gate"]["availability"] == "reserved"
    assert payload["stability_gate"]["feature"]["name"] == "digital_twin"


def test_run_digital_twin_endpoint_returns_graph_snapshot(monkeypatch):
    monkeypatch.setattr(llm_api, "_get_agent", lambda: _FakeAgent())

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            llm_api.run_digital_twin(
                llm_api.DigitalTwinRequest(hostname="SW1", include_telemetry=True, include_sdn=True)
            )
        )

    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.detail["stability_gate"]["feature"]["name"] == "digital_twin"