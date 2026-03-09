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