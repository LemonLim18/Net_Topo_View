from __future__ import annotations

import importlib.util
import json
import sys
from types import SimpleNamespace
from types import ModuleType
from pathlib import Path


class _FakeMCP:
    def resource(self, _uri: str):
        def decorator(func):
            return func

        return decorator


def _load_resources_module():
    fake_server = ModuleType("mcp.server")
    fake_server.db = SimpleNamespace()
    fake_server.mcp = _FakeMCP()

    fake_pkg = ModuleType("mcp")
    fake_pkg.server = fake_server

    old_pkg = sys.modules.get("mcp")
    old_server = sys.modules.get("mcp.server")
    sys.modules["mcp"] = fake_pkg
    sys.modules["mcp.server"] = fake_server

    try:
        resource_path = Path(__file__).resolve().parent / "mcp" / "resources.py"
        spec = importlib.util.spec_from_file_location("test_mcp_resources_module", resource_path)
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


resources = _load_resources_module()


class _FakeFreshness:
    def __init__(self, source: str, scope: str, captured_at: str | None = None):
        self.source = source
        self.scope = scope
        self.captured_at = captured_at

    def to_dict(self):
        return {
            "captured_at": self.captured_at,
            "ingested_at": self.captured_at,
            "source": self.source,
            "scope": self.scope,
            "confidence": 0.9,
            "freshness_class": "live" if self.captured_at else "missing",
            "age_seconds": 0 if self.captured_at else None,
        }


class _FakeBuilder:
    def __init__(self, db_manager=None):
        self.db_manager = db_manager

    def _fetch_inventory(self):
        return SimpleNamespace(devices=[{"hostname": "R1", "management_ip": "10.0.0.1"}])

    def _resolve_device_ips(self, devices, hostnames=None):
        return [device["management_ip"] for device in devices]

    def _fetch_telemetry(self, device_ips, hostnames=None, query=None, routing_decision=None):
        return SimpleNamespace(
            metrics=[{"device": "10.0.0.1", "metric": "cpu", "value": 42, "timestamp": "2026-03-09T10:00:00+00:00", "unit": "%"}],
            source="influxdb",
            query_range="-15m",
            policy_name="recent_latest",
            retrieval_reason="default telemetry window",
            sample_strategy="latest",
            aggregate_every="5m",
            anomaly_baselines=[],
            change_markers=[],
            freshness=_FakeFreshness("influxdb", "device_metrics", "2026-03-09T10:00:00+00:00"),
        )

    def _fetch_config_history(self):
        return SimpleNamespace(
            snapshots=[{"hostname": "R1", "device_id": 1, "captured_at": "2026-03-09T10:01:00+00:00", "config_hash": "abc123"}],
            latest_snapshot_at="2026-03-09T10:01:00+00:00",
            devices_with_snapshots=1,
            devices_missing_snapshots=[],
            source="config_snapshots",
            freshness=_FakeFreshness("config_snapshots", "device_configs", "2026-03-09T10:01:00+00:00"),
        )

    def _fetch_deployment_status(self):
        return SimpleNamespace(
            intents=[{"id": "intent-1", "intent_type": "deploy", "status": "planned", "resolved_devices": ["R1"]}],
            change_requests=[{"id": 14, "title": "Deploy OSPF baseline", "status": "running"}],
            jobs=[{"id": 21, "status": "running", "stage": "apply", "hostname": "R1"}],
            source="intent_store+deployment_pipeline",
            freshness=_FakeFreshness("intent_store+deployment_pipeline", "change_management", "2026-03-09T10:02:00+00:00"),
        )


class _FakeDb:
    def get_topology(self):
        return {
            "timestamp": "2026-03-09T10:03:00+00:00",
            "nodes": {
                "R1": {"mgmt_ip": "10.0.0.1", "down": False, "connectedNode": ["R2"]},
                "R2": {"mgmt_ip": "10.0.0.2", "down": False, "connectedNode": ["R1"]},
            },
        }


def test_device_config_resource_returns_real_payload(monkeypatch):
    monkeypatch.setattr(
        resources.network_tools,
        "get_device_running_config",
        lambda hostname: json.dumps(
            {
                "hostname": hostname,
                "config": "hostname R1\ninterface Gi0/0",
                "snapshot_time": "2026-03-09T10:01:00+00:00",
                "source_metadata": {"source": "config_snapshots", "scope": "device_configs"},
            }
        ),
    )

    payload = json.loads(resources.get_device_config("R1"))

    assert payload["hostname"] == "R1"
    assert "Placeholder" not in payload["config"]
    assert payload["source_metadata"]["source"] == "config_snapshots"


def test_live_topology_resource_uses_authoritative_topology(monkeypatch):
    monkeypatch.setattr(resources, "db", _FakeDb())
    monkeypatch.setattr(resources.network_tools, "set_db_manager", lambda db_manager: None)
    monkeypatch.setattr(
        resources.network_tools,
        "_topology_source_metadata",
        lambda topo: {"source": "crawler_cache", "scope": "live_topology", "captured_at": topo.get("timestamp")},
    )

    payload = json.loads(resources.get_live_topology())

    assert payload["total_devices"] == 2
    assert payload["source_metadata"]["source"] == "crawler_cache"
    assert "R1" in payload["topology"]["nodes"]


def test_telemetry_resource_exposes_snapshot_and_freshness(monkeypatch):
    monkeypatch.setattr(resources, "ContextBundleBuilder", _FakeBuilder)
    monkeypatch.setattr(resources.network_tools, "set_db_manager", lambda db_manager: None)

    payload = json.loads(resources.get_telemetry_snapshot())

    assert payload["total"] == 1
    assert payload["policy_name"] == "recent_latest"
    assert payload["source_metadata"]["source"] == "influxdb"


def test_change_jobs_resource_exposes_intents_requests_and_jobs(monkeypatch):
    monkeypatch.setattr(resources, "ContextBundleBuilder", _FakeBuilder)
    monkeypatch.setattr(resources.network_tools, "set_db_manager", lambda db_manager: None)

    payload = json.loads(resources.get_change_jobs())

    assert payload["intents"][0]["id"] == "intent-1"
    assert payload["change_requests"][0]["title"] == "Deploy OSPF baseline"
    assert payload["jobs"][0]["stage"] == "apply"
    assert payload["source_metadata"]["scope"] == "change_management"


def test_context_health_resource_combines_health_and_sync(monkeypatch):
    monkeypatch.setattr(resources.network_tools, "set_db_manager", lambda db_manager: None)
    monkeypatch.setattr(resources.network_tools, "get_context_health", lambda: json.dumps({"versions": {"live_topology": 4}}))
    monkeypatch.setattr(resources.network_tools, "get_last_sync_status", lambda: json.dumps({"operations": {"inventory": {"status": "idle"}}}))

    payload = json.loads(resources.get_context_health_resource())

    assert payload["context_health"]["versions"]["live_topology"] == 4
    assert payload["last_sync_status"]["operations"]["inventory"]["status"] == "idle"