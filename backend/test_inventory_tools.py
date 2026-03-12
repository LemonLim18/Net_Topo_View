import json
from types import SimpleNamespace

from db.models import Device
from llm import network_tools


class _InventoryFakeConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        class _Rows:
            def fetchall(self_nonlocal):
                return [
                    {
                        "src_hostname": "R1",
                        "src_interface": "Gig0/0",
                        "dst_hostname": "SW1",
                        "dst_interface": "Gig0/1",
                        "discovery_protocol": "manual",
                        "updated_at": "2026-03-09T10:00:00+00:00",
                    }
                ]

        return _Rows()


class _InventoryFakeDb:
    def get_all_devices(self):
        return [
            Device(hostname="R1", management_ip="10.0.0.1", vendor="cisco_ios", status="active", role="core", source="manual", updated_at="2026-03-09T10:00:00+00:00"),
            Device(hostname="SW1", management_ip="10.0.0.2", vendor="huawei_vrp", status="planned", role="access", source="manual", updated_at="2026-03-09T10:05:00+00:00"),
        ]

    def get_topology(self):
        return {
            "timestamp": "2026-03-09T10:06:00+00:00",
            "nodes": {
                "R1": {"mgmt_ip": "10.0.0.1", "down": False},
                "FW1": {"mgmt_ip": "10.0.0.254", "down": False},
            },
        }

    def _connect(self):
        return _InventoryFakeConn()


class _NetBoxDevice:
    def __init__(self, name, ip, platform, model, status, role, updated_at):
        self.name = name
        self.primary_ip4 = ip
        self.platform = SimpleNamespace(slug=platform)
        self.device_type = SimpleNamespace(model=model)
        self.status = SimpleNamespace(value=status)
        self.role = SimpleNamespace(slug=role)
        self.last_updated = updated_at


class _NetBoxTopologyClient:
    def __init__(self, devices):
        self._devices = devices
        self.api = object()

    def get_devices(self):
        return self._devices


class _FakeTopologySyncer:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def get_planned_topology(self):
        return {
            "nodes": {
                "NB1": {
                    "mgmt_ip": "10.9.9.1",
                    "connectedNode": ["NB2"],
                    "edgeLink": {
                        "Eth1": {
                            "remote_node": "NB2",
                            "remote_interface": "Eth2",
                            "remote_ip": "10.9.9.2",
                        }
                    },
                },
                "NB2": {
                    "mgmt_ip": "10.9.9.2",
                    "connectedNode": ["NB1"],
                    "edgeLink": {
                        "Eth2": {
                            "remote_node": "NB1",
                            "remote_interface": "Eth1",
                            "remote_ip": "10.9.9.1",
                        }
                    },
                },
            },
            "source": "netbox",
            "timestamp": "2026-03-09T10:15:00+00:00",
            "device_count": 2,
        }


def test_list_inventory_devices_uses_local_fallback():
    network_tools.set_db_manager(_InventoryFakeDb())
    payload = json.loads(network_tools.list_inventory_devices())

    assert payload["total"] == 2
    assert payload["source_metadata"]["scope"] == "planned_inventory"


def test_get_inventory_device_returns_single_device():
    network_tools.set_db_manager(_InventoryFakeDb())
    payload = json.loads(network_tools.get_inventory_device("sw1"))

    assert payload["device"]["hostname"] == "SW1"
    assert payload["device"]["management_ip"] == "10.0.0.2"


def test_get_inventory_device_supports_fuzzy_alias_lookup():
    network_tools.set_db_manager(_InventoryFakeDb())
    payload = json.loads(network_tools.get_inventory_device("access switch huawei 1"))

    assert payload["device"]["hostname"] == "SW1"
    assert payload["resolved_hostname"] == "SW1"


def test_get_inventory_device_uses_local_alias_fast_path_before_netbox(monkeypatch):
    network_tools.set_db_manager(_InventoryFakeDb())

    def _unexpected_netbox_fetch():
        raise AssertionError("NetBox should not be queried for local fuzzy alias resolution")

    monkeypatch.setattr(network_tools, "_inventory_records_from_netbox", _unexpected_netbox_fetch)

    payload = json.loads(network_tools.get_inventory_device("core cisco 1"))

    assert payload["device"]["hostname"] == "R1"
    assert payload["source_used"] == "local_db"


def test_search_inventory_devices_supports_natural_language_alias_query():
    network_tools.set_db_manager(_InventoryFakeDb())
    payload = json.loads(network_tools.search_inventory_devices("What is the management IP of access switch huawei 1 in inventory?"))

    assert payload["total"] >= 1
    assert payload["matches"][0]["hostname"] == "SW1"


def test_compare_planned_vs_live_separates_state():
    network_tools.set_db_manager(_InventoryFakeDb())
    payload = json.loads(network_tools.compare_planned_vs_live())

    assert payload["planned_only"] == ["SW1"]
    assert payload["live_only"] == ["FW1"]
    assert payload["semantics_map"]["version"] == "phase6_step22_v1"


def test_compare_planned_vs_live_supports_fuzzy_alias_lookup():
    network_tools.set_db_manager(_InventoryFakeDb())
    payload = json.loads(network_tools.compare_planned_vs_live("core cisco 1"))

    assert payload["devices"][0]["hostname"] == "R1"


def test_inventory_surfaces_include_canonical_semantics_fields():
    network_tools.set_db_manager(_InventoryFakeDb())

    inventory_payload = json.loads(network_tools.list_inventory_devices())
    device_payload = json.loads(network_tools.get_inventory_device("R1"))
    planned_payload = json.loads(network_tools.get_planned_topology())

    r1 = next(device for device in inventory_payload["devices"] if device["hostname"] == "R1")
    sw1 = next(device for device in inventory_payload["devices"] if device["hostname"] == "SW1")

    assert inventory_payload["semantics_map"]["version"] == "phase6_step22_v1"
    assert r1["presence_state"] == "planned_and_live"
    assert sw1["presence_state"] == "planned_only"
    assert device_payload["device"]["management_ip_alignment"] == "match"
    assert planned_payload["links"][0]["interface_presence_state"] == "planned_only"


def test_get_planned_topology_returns_persistent_links():
    network_tools.set_db_manager(_InventoryFakeDb())
    payload = json.loads(network_tools.get_planned_topology())

    assert payload["total"] == 1
    assert payload["links"][0]["src_hostname"] == "R1"


def test_list_inventory_devices_prefers_netbox(monkeypatch):
    network_tools.set_db_manager(_InventoryFakeDb())
    client = _NetBoxTopologyClient(
        [
            _NetBoxDevice(
                name="NB1",
                ip="10.9.9.1/24",
                platform="cisco-ios",
                model="C9300",
                status="active",
                role="core",
                updated_at="2026-03-09T10:10:00+00:00",
            )
        ]
    )

    monkeypatch.setattr("netbox.client.NetBoxClient.get_client", lambda: client)

    payload = json.loads(network_tools.list_inventory_devices())

    assert payload["source_used"] == "netbox"
    assert payload["devices"][0]["hostname"] == "NB1"
    assert payload["captured_at"] == "2026-03-09T10:10:00+00:00"


def test_get_inventory_device_falls_back_to_local_when_netbox_misses(monkeypatch):
    network_tools.set_db_manager(_InventoryFakeDb())
    client = _NetBoxTopologyClient(
        [
            _NetBoxDevice(
                name="NB1",
                ip="10.9.9.1/24",
                platform="cisco-ios",
                model="C9300",
                status="active",
                role="core",
                updated_at="2026-03-09T10:10:00+00:00",
            )
        ]
    )

    monkeypatch.setattr("netbox.client.NetBoxClient.get_client", lambda: client)

    payload = json.loads(network_tools.get_inventory_device("SW1"))

    assert payload["source_used"] == "local_db"
    assert payload["device"]["hostname"] == "SW1"


def test_get_planned_topology_prefers_netbox(monkeypatch):
    network_tools.set_db_manager(_InventoryFakeDb())
    monkeypatch.setattr("netbox.sync_topology.TopologySyncer", _FakeTopologySyncer)

    payload = json.loads(network_tools.get_planned_topology())

    assert payload["source_used"] == "netbox"
    assert payload["total"] == 1
    assert payload["topology"]["device_count"] == 2
    assert payload["captured_at"] == "2026-03-09T10:15:00+00:00"


def test_list_all_devices_supports_inventory_scope():
    network_tools.set_db_manager(_InventoryFakeDb())
    payload = json.loads(network_tools.list_all_devices(scope="inventory"))

    assert payload["scope_requested"] == "inventory"
    assert payload["source_used"] == "local_db"
    assert payload["devices"][0]["scope"] == "inventory"
    assert payload["devices"][0]["source_label"] == "local_db"


def test_list_all_devices_supports_both_scope_without_flattening():
    network_tools.set_db_manager(_InventoryFakeDb())
    payload = json.loads(network_tools.list_all_devices(scope="both"))

    assert payload["scope_requested"] == "both"
    r1 = next(device for device in payload["devices"] if device["hostname"] == "R1")
    fw1 = next(device for device in payload["devices"] if device["hostname"] == "FW1")
    sw1 = next(device for device in payload["devices"] if device["hostname"] == "SW1")

    assert r1["live"]["scope"] == "live"
    assert r1["inventory"]["scope"] == "inventory"
    assert fw1["inventory"] is None
    assert sw1["live"] is None