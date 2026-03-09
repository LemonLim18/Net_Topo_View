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