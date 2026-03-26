import sys
from types import ModuleType, SimpleNamespace

sys.modules.setdefault("pynetbox", ModuleType("pynetbox"))

from netbox.client import NetBoxClient


class _FakeDevicesEndpoint:
    def __init__(self):
        self.created_payload = None

    def get(self, **kwargs):
        return None

    def create(self, payload):
        self.created_payload = payload
        return SimpleNamespace(id=101, name=payload["name"])


class _FakeApi:
    def __init__(self):
        self.dcim = SimpleNamespace(devices=_FakeDevicesEndpoint())


def _build_client():
    client = NetBoxClient.__new__(NetBoxClient)
    client.api = _FakeApi()
    client.url = "http://netbox.local"
    client.token = "token"
    client.timeout_seconds = 10.0
    return client


def test_create_device_includes_custom_fields_in_initial_payload(monkeypatch):
    client = _build_client()
    monkeypatch.setattr(client, "_ensure_site", lambda slug="default", name="Default Site": 1)
    monkeypatch.setattr(client, "_ensure_device_type", lambda vendor, model=None: 2)
    monkeypatch.setattr(client, "_ensure_device_role", lambda role="network-device": 3)
    monkeypatch.setattr(client, "_ensure_platform", lambda vendor: 4)
    monkeypatch.setattr(client, "_set_device_custom_fields", lambda *args, **kwargs: None)

    client.create_device(
        name="Edge-1",
        vendor="cisco_ios",
        model="ISR",
        role="core",
        ssh_user="admin",
        ssh_password="secret123",
        os_version="17.9.1",
    )

    payload = client.api.dcim.devices.created_payload
    assert payload is not None
    assert payload["custom_fields"]["ssh_user"] == "admin"
    assert payload["custom_fields"]["ssh_password"] == "secret123"
    assert payload["custom_fields"]["os_version"] == "17.9.1"


def test_create_device_omits_blank_custom_fields(monkeypatch):
    client = _build_client()
    monkeypatch.setattr(client, "_ensure_site", lambda slug="default", name="Default Site": 1)
    monkeypatch.setattr(client, "_ensure_device_type", lambda vendor, model=None: 2)
    monkeypatch.setattr(client, "_ensure_device_role", lambda role="network-device": 3)
    monkeypatch.setattr(client, "_ensure_platform", lambda vendor: 4)
    monkeypatch.setattr(client, "_set_device_custom_fields", lambda *args, **kwargs: None)

    client.create_device(
        name="Edge-2",
        vendor="cisco_ios",
        ssh_user="   ",
        ssh_password="",
        os_version=None,
    )

    payload = client.api.dcim.devices.created_payload
    assert payload is not None
    assert "custom_fields" not in payload
