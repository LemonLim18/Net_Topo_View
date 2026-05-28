from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


class _FakeMCP:
    def tool(self):
        def decorator(func):
            return func

        return decorator


def _load_tools_module():
    fake_server = ModuleType("mcp.server")
    fake_server.db = SimpleNamespace()
    fake_server.mcp = _FakeMCP()
    fake_server.agent = None
    fake_server.pipeline = None

    fake_pkg = ModuleType("mcp")
    fake_pkg.server = fake_server

    old_pkg = sys.modules.get("mcp")
    old_server = sys.modules.get("mcp.server")
    sys.modules["mcp"] = fake_pkg
    sys.modules["mcp.server"] = fake_server

    try:
        tool_path = Path(__file__).resolve().parent.parent.parent / "mcp" / "tools.py"
        spec = importlib.util.spec_from_file_location("test_mcp_tools_module", tool_path)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if old_pkg is not None:
            sys.modules["mcp"] = old_pkg
        else:
            sys.modules.pop("mcp", None)
        if old_server is not None:
            sys.modules["mcp.server"] = old_server
        else:
            sys.modules.pop("mcp.server", None)


tools = _load_tools_module()


def test_list_devices_uses_canonical_network_tool(monkeypatch):
    monkeypatch.setattr(tools.network_tools, "set_db_manager", lambda db_manager: None)
    monkeypatch.setattr(
        tools.network_tools,
        "list_all_devices",
        lambda scope="both": json.dumps({"devices": [{"hostname": "R1", "presence_state": "planned_and_live"}], "scope_requested": scope, "authority_map_version": "phase4_step13_v1"}),
    )

    payload = json.loads(tools.list_devices())

    assert payload["devices"][0]["hostname"] == "R1"
    assert payload["authority_map_version"] == "phase4_step13_v1"


def test_get_device_details_combines_inventory_live_and_canonical_state(monkeypatch):
    monkeypatch.setattr(tools.network_tools, "set_db_manager", lambda db_manager: None)
    monkeypatch.setattr(
        tools.network_tools,
        "get_inventory_device",
        lambda hostname: json.dumps({"device": {"hostname": hostname, "presence_state": "planned_and_live"}, "authority_map_version": "phase4_step13_v1", "semantics_map": {"version": "phase6_step22_v1"}}),
    )
    monkeypatch.setattr(
        tools.network_tools,
        "get_device_details",
        lambda hostname: json.dumps({"hostname": hostname, "status": "up"}),
    )
    monkeypatch.setattr(
        tools.network_tools,
        "compare_planned_vs_live",
        lambda hostname="": json.dumps({"devices": [{"hostname": hostname or "R1", "presence_state": "planned_and_live"}], "authority_map_version": "phase4_step13_v1", "semantics_map": {"version": "phase6_step22_v1"}}),
    )

    payload = json.loads(tools.get_device_details("R1"))

    assert payload["canonical_state"]["presence_state"] == "planned_and_live"
    assert payload["live"]["status"] == "up"
    assert payload["authority_map_version"] == "phase4_step13_v1"


def test_run_command_fails_closed_without_mock_output():
    payload = json.loads(tools.run_command("R1", "show version"))

    assert payload["status"] == "rejected"
    assert "mock" not in json.dumps(payload).lower()
    assert "get_device_details" in payload["allowed_alternatives"]
