from types import SimpleNamespace
import sys
import types

# Some environments running unit tests don't have NetBox deps installed.
# Stub pynetbox so importing netbox.client works.
if "pynetbox" not in sys.modules:
    sys.modules["pynetbox"] = types.ModuleType("pynetbox")

from compliance.scope_resolver import NetBoxScopeResolver


class _FakeNbDev:
    def __init__(self):
        self.role = SimpleNamespace(slug="access-switch", name="Access Switch")
        self.platform = SimpleNamespace(slug="cisco-ios", name="Cisco IOS")
        self.site = SimpleNamespace(slug="hq", name="HQ")
        self.tenant = None
        self.tags = [SimpleNamespace(slug="campus", name="campus")]


def test_scope_resolver_fallback_when_no_netbox(monkeypatch):
    r = NetBoxScopeResolver(ttl_seconds=1)
    # Force no api
    r.nb.api = None

    db = SimpleNamespace(get_device_by_id=lambda _id: SimpleNamespace(id=_id, hostname="SW1", vendor="cisco_ios", role="access", netbox_id=123))
    out = r.resolve_device(db, 1)
    assert out.partial
    assert out.device["hostname"] == "SW1"


def test_scope_resolver_enriches_groups(monkeypatch):
    r = NetBoxScopeResolver(ttl_seconds=60)
    r.nb.api = object()
    r.nb.get_device_by_id = lambda _id: _FakeNbDev()
    r.nb.get_device_by_name = lambda _name: []

    db = SimpleNamespace(get_device_by_id=lambda _id: SimpleNamespace(id=_id, hostname="SW1", vendor="cisco_ios", role="access", netbox_id=123))
    out = r.resolve_device(db, 1)
    assert not out.partial
    assert out.device.get("netbox_role_slug") == "access-switch"
    assert "site:hq" in (out.device.get("device_groups") or [])
    assert "tag:campus" in (out.device.get("device_groups") or [])

