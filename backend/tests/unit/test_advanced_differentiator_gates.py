import asyncio

import pytest

from agent import api as agent_api
from llm.controller_scope import build_controller_implementation_status
from rag import api as rag_api


class _ReservedAgent:
    @property
    def controller_implementation_status(self):
        return build_controller_implementation_status(
            [
                "get_context_health",
                "get_inventory_device",
                "compare_planned_vs_live",
                "parse_intent",
                "validate_intent",
                "plan_change",
            ]
        )


class _FakeDbManager:
    def __init__(self, agent):
        self.agent = agent


def test_agent_plan_requires_step19_unlock(monkeypatch):
    monkeypatch.setattr(agent_api, "_agent", _ReservedAgent())

    with pytest.raises(Exception) as exc_info:
        asyncio.run(agent_api.plan_action(agent_api.PlanRequest(message="Shut down an access port")))

    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.detail["stability_gate"]["feature"]["name"] == "autonomous_remediation"


def test_agent_execute_requires_step19_unlock(monkeypatch):
    monkeypatch.setattr(agent_api, "_agent", _ReservedAgent())

    with pytest.raises(Exception) as exc_info:
        asyncio.run(agent_api.execute_plan(agent_api.ExecuteRequest(plan_id="plan-1", action="generate")))

    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.detail["stability_gate"]["feature"]["name"] == "autonomous_remediation"


def test_rag_digital_twin_preview_requires_step19_unlock(monkeypatch):
    monkeypatch.setattr(rag_api, "_db_manager", _FakeDbManager(_ReservedAgent()))

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            rag_api.preview_digital_twin(
                rag_api.DigitalTwinPreviewRequest(device_hostnames=["SW1"]),
                current_user={"username": "operator"},
            )
        )

    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.detail["stability_gate"]["feature"]["name"] == "digital_twin"