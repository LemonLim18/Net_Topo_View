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


class _AgentFakeDb:
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
        return {
            "timestamp": "2026-03-09T10:15:00+00:00",
            "nodes": {
                "R1": {"mgmt_ip": "10.0.0.1", "down": False, "connectedNode": ["SW1"], "interfaceList": [{}], "edgeLink": {}},
                "SW1": {"mgmt_ip": "10.0.0.2", "down": False, "connectedNode": ["R1"], "interfaceList": [{}], "edgeLink": {}},
            },
        }

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


def test_planned_vs_live_query_prefetches_comparison_tool():
    import asyncio

    provider, response = asyncio.run(_run_chat("Compare planned vs live state for SW1"))

    assert response["content"] == "authoritative answer"
    first_call = provider.calls[0]
    tool_messages = [message for message in first_call["messages"] if message.role == "tool"]
    assert any(message.name == "compare_planned_vs_live" for message in tool_messages)


def test_troubleshooting_query_prefetches_multi_source_evidence():
    import asyncio

    provider, _ = asyncio.run(_run_chat("Troubleshoot why SW1 is down"))

    first_call = provider.calls[0]
    tool_messages = [message for message in first_call["messages"] if message.role == "tool"]
    tool_names = {message.name for message in tool_messages}
    assert "get_device_details" in tool_names
    assert "get_device_running_config" in tool_names
    assert "get_recent_incidents" in tool_names