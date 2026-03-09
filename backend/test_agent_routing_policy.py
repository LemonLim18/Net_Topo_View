import json

from db.models import Device
from llm.agent import NetworkAgent
from llm.provider import LLMProvider, LLMMessage, LLMResponse


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


async def _run_chat(message: str):
    provider = _FakeProvider()
    agent = NetworkAgent(db_manager=_AgentFakeDb(), provider=provider, retriever=None)
    response = await agent.chat(message)
    return provider, response


async def _run_chat_for_db(message: str, db):
    provider = _FakeProvider()
    agent = NetworkAgent(db_manager=db, provider=provider, retriever=None)
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
    assert "Grounding: planned;" in response["content"]


def test_planned_vs_live_query_prefetches_comparison_tool():
    import asyncio

    provider, response = asyncio.run(_run_chat("Compare planned vs live state for SW1"))

    assert response["content"].startswith("authoritative answer")
    first_call = provider.calls[0]
    tool_messages = [message for message in first_call["messages"] if message.role == "tool"]
    assert any(message.name == "compare_planned_vs_live" for message in tool_messages)


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
    agent = NetworkAgent(db_manager=db, provider=provider, retriever=None)

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