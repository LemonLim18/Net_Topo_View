import json
from copy import deepcopy

from db.models import Device
from llm.agent import NetworkAgent
from llm.proposal import ConfigProposal
from llm.provider import LLMProvider, LLMMessage, LLMResponse, ToolCall


class _FakeProvider(LLMProvider):
    def __init__(self):
        self.calls = []

    async def generate(self, messages, tools=None, temperature=0.3):
        self.calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        return LLMResponse(content="authoritative answer", tool_calls=[])

    def model_name(self) -> str:
        return "fake"


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        class _Rows:
            def fetchall(self_nonlocal):
                return []

            def fetchone(self_nonlocal):
                return None

        return _Rows()


class _FakeSnapshot:
    def __init__(self):
        self.config_text = "hostname SW1"
        self.config_hash = "abc123"
        self.captured_at = "2026-03-09T10:10:00+00:00"


class _FakeDriftEvent:
    def __init__(self, hostname: str = "SW1", severity: str = "high"):
        self.device_id = 1
        self.hostname = hostname
        self.has_drift = True
        self.severity = severity
        self.timestamp = "2026-03-09T10:12:00+00:00"


class _AgentFakeDb:
    def __init__(self):
        self._topology = {
            "timestamp": "2026-03-09T10:15:00+00:00",
            "nodes": {
                "R1": {"mgmt_ip": "10.0.0.1", "down": False, "connectedNode": ["SW1"], "interfaceList": [{}], "edgeLink": {}},
                "SW1": {"mgmt_ip": "10.0.0.2", "down": False, "connectedNode": ["R1"], "interfaceList": [{}], "edgeLink": {}},
            },
        }

    def get_all_devices(self):
        return [
            Device(hostname="SW1", management_ip="10.0.0.2", vendor="cisco_ios", status="active", role="access", source="manual", updated_at="2026-03-09T10:00:00+00:00", id=1),
            Device(hostname="R1", management_ip="10.0.0.1", vendor="cisco_ios", status="active", role="core", source="manual", updated_at="2026-03-09T10:00:00+00:00", id=2),
        ]

    def get_device_by_hostname(self, hostname):
        for device in self.get_all_devices():
            if device.hostname.lower() == hostname.lower():
                return device
        return None

    def get_topology(self):
        return self._topology

    def set_topology(self, topology):
        self._topology = topology

    def get_config_history(self, device_id, limit=1):
        return [_FakeSnapshot()]

    def get_drift_events(self, limit=10):
        return []

    def _connect(self):
        return _FakeConn()


class _MemoryProposalManager:
    def __init__(self):
        self._items = {}

    def create(
        self,
        user_prompt,
        target_device,
        target_device_id,
        vendor,
        feature,
        params,
        generated_config,
        rollback_config="",
        diff_text="",
        risk_level="medium",
        ai_explanation="",
    ):
        proposal = ConfigProposal(
            id=f"proposal-{len(self._items) + 1}",
            status="pending",
            user_prompt=user_prompt,
            target_device=target_device,
            target_device_id=target_device_id,
            vendor=vendor,
            feature=feature,
            params=deepcopy(params),
            generated_config=generated_config,
            rollback_config=rollback_config,
            diff_text=diff_text,
            risk_level=risk_level,
            ai_explanation=ai_explanation,
            created_at="2026-03-10T00:00:00+00:00",
            updated_at="2026-03-10T00:00:00+00:00",
        )
        self._items[proposal.id] = proposal
        return proposal


async def _run_chat(message: str):
    provider = _FakeProvider()
    agent = NetworkAgent(
        db_manager=_AgentFakeDb(),
        provider=provider,
        retriever=None,
        proposal_manager=_MemoryProposalManager(),
    )
    response = await agent.chat(message)
    return provider, response


async def _run_chat_for_db(message: str, db):
    provider = _FakeProvider()
    agent = NetworkAgent(
        db_manager=db,
        provider=provider,
        retriever=None,
        proposal_manager=_MemoryProposalManager(),
    )
    response = await agent.chat(message)
    return provider, response


async def _run_chat_with_agent(agent: NetworkAgent, message: str):
    return await agent.chat(message)


def test_inventory_query_prefetches_inventory_tool():
    import asyncio

    provider, response = asyncio.run(_run_chat("What is the management IP of SW1 in inventory?"))

    assert response["type"] == "text"
    first_call = provider.calls[0]
    tool_messages = [message for message in first_call["messages"] if message.role == "tool"]
    assert any(message.name == "get_inventory_device" for message in tool_messages)
    tool_names = {tool["name"] for tool in first_call["tools"]}
    assert "get_inventory_device" in tool_names
    assert "list_all_devices" not in tool_names
    assert response["grounding_meta"]["answer_scope"] == "planned"
    assert "local_db" in response["grounding_meta"]["source_used"]
    assert response["grounding_meta"]["source_age"][0]["scope"] == "planned_inventory"
    assert response["grounding_meta"]["answer_policy"]["name"] == "management_ip_lookup"
    assert response["grounding_meta"]["answer_policy"]["satisfied"] is True
    assert response["grounding_meta"]["answer_policy"]["required_tools"] == ["get_inventory_device"]
    assert response["grounding_meta"]["evidence_record"]["version"] == "phase2_step6_v1"
    assert response["grounding_meta"]["structured_rationale"]["verification_plan"]
    assert response["grounding_meta"]["controller_flow"]["release"]["decision"] == "allow"
    assert response["grounding_meta"]["controller_flow"]["observe"]["satisfied_domains"] == ["planned_inventory"]
    assert "Current state:" in response["content"]
    assert "Target state:" in response["content"]
    assert "Verification plan:" in response["content"]
    assert "Grounding: planned;" in response["content"]


def test_system_prompt_includes_controller_scope_contract():
    import asyncio

    provider, _ = asyncio.run(_run_chat("What is the management IP of SW1 in inventory?"))

    first_system_prompt = provider.calls[0]["messages"][0].content
    assert "## Controller Scope Contract" in first_system_prompt
    assert "## Milestone 2 Foundation Baseline" in first_system_prompt
    assert "## Instruction Hierarchy" in first_system_prompt
    assert "## Controller Policy" in first_system_prompt
    assert "Reasoning policy: private_reasoning_structured_rationale_only" in first_system_prompt
    assert "If a requested capability is partial or not_implemented" in first_system_prompt


class _BypassPlanProvider(LLMProvider):
    def __init__(self):
        self.calls = []
        self._main_calls = 0

    async def generate(self, messages, tools=None, temperature=0.3):
        self.calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        first_system = messages[0].content if messages else ""

        if "You are an **intent parser**" in first_system:
            payload = {
                "intent_type": "configure",
                "action": "create_vlan",
                "feature": "vlan",
                "vendor": "cisco_ios",
                "parameters": {"vlans": [{"id": 20, "name": "Users"}]},
                "scope": {"device_hostnames": ["SW1"], "all_devices": False},
                "risk_level": "medium",
                "justification": "Create VLAN 20 on SW1",
                "resolved_devices": ["SW1"],
            }
            return LLMResponse(content=json.dumps(payload), tool_calls=[])

        self._main_calls += 1
        if self._main_calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="plan_change", arguments={"intent_json": "{\"id\":\"bad\"}"})],
            )
        return LLMResponse(content="proposed safely", tool_calls=[])

    def model_name(self) -> str:
        return "bypass-test"


async def _run_chat_with_provider(message: str, provider):
    agent = NetworkAgent(
        db_manager=_AgentFakeDb(),
        provider=provider,
        retriever=None,
        proposal_manager=_MemoryProposalManager(),
    )
    response = await agent.chat(message)
    return provider, response


def test_change_request_bypass_is_recovered_by_controller_policy():
    import asyncio

    provider, response = asyncio.run(_run_chat_with_provider("Configure VLAN 20 on SW1", _BypassPlanProvider()))

    assert response["type"] in {"proposal", "intent_plan"}
    assert response["proposal"]["feature"] == "vlan"
    assert "generated_config" in response["proposal"]
    assert response["grounding_meta"]["controller_flow"]["release"]["decision"] == "allow"
    assert response["grounding_meta"]["controller_flow"]["observe"]["change_workflow"]["satisfied"] is True
    assert "knowledge_base" in response["grounding_meta"]["controller_flow"]["observe"]["satisfied_domains"]
    tool_messages = [message for call in provider.calls for message in call["messages"] if message.role == "tool"]
    assert any(message.name == "parse_intent" for message in tool_messages)
    assert any(message.name == "validate_intent" for message in tool_messages)
    assert any(message.name == "get_template_schema" for message in tool_messages)


def test_inventory_query_with_missing_authoritative_evidence_is_blocked():
    import asyncio

    class _MissingInventoryDb(_AgentFakeDb):
        def get_device_by_hostname(self, hostname):
            return None

    _, response = asyncio.run(_run_chat_for_db("What is the management IP of SW9 in inventory?", _MissingInventoryDb()))

    assert response["type"] == "text"
    assert response["grounding_meta"]["controller_flow"]["release"]["decision"] == "block"
    assert response["grounding_meta"]["controller_flow"]["release"]["missing_domains"] == ["planned_inventory"]
    assert response["content"].startswith("I can't provide a grounded answer yet.")


def test_planned_vs_live_query_prefetches_comparison_tool():
    import asyncio

    provider, response = asyncio.run(_run_chat("Compare planned vs live state for SW1"))

    assert response["content"].startswith("authoritative answer")
    first_call = provider.calls[0]
    tool_messages = [message for message in first_call["messages"] if message.role == "tool"]
    assert any(message.name == "compare_planned_vs_live" for message in tool_messages)


def test_planned_vs_live_query_exposes_digital_twin_tool():
    import asyncio

    provider, _ = asyncio.run(_run_chat("Compare planned vs live state for SW1"))

    first_call = provider.calls[0]
    tool_names = {tool["name"] for tool in first_call["tools"]}
    assert "get_digital_twin" in tool_names


def test_planned_vs_live_query_returns_mixed_grounding():
    import asyncio

    _, response = asyncio.run(_run_chat("Compare planned vs live state for SW1"))

    assert response["grounding_meta"]["answer_scope"] == "mixed"
    assert "crawler_cache" in response["grounding_meta"]["source_used"]
    assert "local_db" in response["grounding_meta"]["source_used"]
    assert "Grounding: mixed;" in response["content"]


def test_live_query_returns_live_grounding_and_confidence():
    import asyncio

    _, response = asyncio.run(_run_chat("Show me the live topology"))

    assert response["grounding_meta"]["answer_scope"] == "live"
    assert "crawler_cache" in response["grounding_meta"]["source_used"]
    assert response["grounding_meta"]["confidence"]["level"] in {"medium", "high"}
    assert "source_age=" in response["content"]


def test_live_query_telemetry_failure_self_corrects_with_fallback():
    import asyncio

    provider = _FakeProvider()
    agent = NetworkAgent(
        db_manager=_AgentFakeDb(),
        provider=provider,
        retriever=None,
        proposal_manager=_MemoryProposalManager(),
    )

    original_execute = agent._tools.execute

    def _execute_with_telemetry_failure(name, arguments):
        if name == "get_device_telemetry_snapshot":
            return json.dumps({"error": "telemetry backend timeout"})
        return original_execute(name, arguments)

    agent._tools.execute = _execute_with_telemetry_failure  # type: ignore[method-assign]

    response = asyncio.run(_run_chat_with_agent(agent, "Is SW1 down right now?"))

    self_correction = response["grounding_meta"]["self_correction"]
    assert self_correction["attempts"]
    assert any(item["failure_kind"] == "missing_telemetry" for item in self_correction["attempts"])
    assert any(
        any(action["name"] == "get_digital_twin" for action in item["revised_actions"])
        for item in self_correction["attempts"]
    )
    assert response["grounding_meta"]["controller_flow"]["release"]["decision"] == "allow"
    assert "telemetry" in response["grounding_meta"]["controller_flow"]["observe"]["satisfied_domains"]
    assert not self_correction["escalations"]


def test_troubleshooting_query_prefetches_multi_source_evidence():
    import asyncio

    provider, _ = asyncio.run(_run_chat("Troubleshoot why SW1 is down"))

    first_call = provider.calls[0]
    tool_messages = [message for message in first_call["messages"] if message.role == "tool"]
    tool_names = {message.name for message in tool_messages}
    assert "get_device_details" in tool_names
    assert "get_device_telemetry_snapshot" in tool_names
    assert "get_device_running_config" in tool_names
    assert "get_recent_incidents" in tool_names


def test_change_planning_route_exposes_change_intelligence_tool():
    import asyncio

    provider, _ = asyncio.run(_run_chat("Plan change for VLAN 20 on SW1"))

    first_call = provider.calls[0]
    tool_names = {tool["name"] for tool in first_call["tools"]}
    assert "analyze_change_intelligence" in tool_names


def test_planned_vs_live_answer_surfaces_management_ip_conflict():
    import asyncio

    class _MismatchDb(_AgentFakeDb):
        def get_topology(self):
            topo = super().get_topology()
            topo["nodes"]["SW1"]["mgmt_ip"] = "10.0.0.22"
            return topo

    _, response = asyncio.run(_run_chat_for_db("Compare planned vs live state for SW1", _MismatchDb()))

    conflicts = response["grounding_meta"]["conflicts"]
    assert any(conflict["code"] == "management_ip_mismatch" for conflict in conflicts)
    assert "Conflicts:" in response["content"]


def test_troubleshooting_answer_surfaces_drift_and_missing_config_conflicts(monkeypatch):
    import asyncio

    class _ConflictDb(_AgentFakeDb):
        def get_config_history(self, device_id, limit=1):
            return []

        def get_drift_events(self, limit=10):
            return [_FakeDriftEvent()]

    class _FakeIncidentStore:
        def list_all(self, status=None, limit=10):
            return [
                {
                    "id": "INC-1",
                    "severity": "high",
                    "status": "open",
                    "affected_devices": '["SW1"]',
                    "updated_at": "2026-03-09T10:13:00+00:00",
                }
            ]

    monkeypatch.setattr("incidents.store.IncidentStore", _FakeIncidentStore)

    _, response = asyncio.run(_run_chat_for_db("Troubleshoot why SW1 is down", _ConflictDb()))

    codes = {conflict["code"] for conflict in response["grounding_meta"]["conflicts"]}
    assert "config_snapshot_missing" in codes
    assert "config_drift_detected" in codes
    assert "active_incident" in codes


def test_agent_rebuilds_context_on_next_turn_without_manual_prompt_refresh():
    import asyncio

    db = _AgentFakeDb()
    provider = _FakeProvider()
    agent = NetworkAgent(
        db_manager=db,
        provider=provider,
        retriever=None,
        proposal_manager=_MemoryProposalManager(),
    )

    asyncio.run(_run_chat_with_agent(agent, "Show me the live topology"))
    first_system_prompt = provider.calls[0]["messages"][0].content
    assert "Management IP: 10.0.0.2" in first_system_prompt

    db.set_topology(
        {
            "timestamp": "2026-03-09T10:20:00+00:00",
            "nodes": {
                "R1": {"mgmt_ip": "10.0.0.1", "down": False, "connectedNode": ["SW1"], "interfaceList": [{}], "edgeLink": {}},
                "SW1": {"mgmt_ip": "10.0.0.22", "down": False, "connectedNode": ["R1"], "interfaceList": [{}], "edgeLink": {}},
            },
        }
    )
    agent.refresh_context()

    asyncio.run(_run_chat_with_agent(agent, "Show me the live topology again"))
    second_system_prompt = provider.calls[1]["messages"][0].content
    assert "Management IP: 10.0.0.22" in second_system_prompt
    assert "Management IP: 10.0.0.2\n" not in second_system_prompt


def test_required_tool_policy_executes_before_accepting_early_answer():
    import asyncio

    provider = _FakeProvider()
    agent = NetworkAgent(
        db_manager=_AgentFakeDb(),
        provider=provider,
        retriever=None,
        proposal_manager=_MemoryProposalManager(),
    )

    original_build_route_plan = agent._build_route_plan

    def _route_plan_without_prefetch(user_message, query_class, hostnames=None):
        route_plan = original_build_route_plan(user_message, query_class, hostnames)
        route_plan["prefetch_tools"] = []
        return route_plan

    agent._build_route_plan = _route_plan_without_prefetch  # type: ignore[method-assign]

    response = asyncio.run(_run_chat_with_agent(agent, "What is the management IP of SW1 in inventory?"))

    assert len(provider.calls) == 2
    second_call_tool_messages = [message for message in provider.calls[1]["messages"] if message.role == "tool"]
    assert any(message.name == "get_inventory_device" for message in second_call_tool_messages)
    assert response["grounding_meta"]["answer_policy"]["satisfied"] is True
    assert response["grounding_meta"]["answer_policy"]["executed_required_tools"] == ["get_inventory_device"]