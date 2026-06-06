import pytest
from types import SimpleNamespace

from config_engine.renderer import TemplateRenderer
from onboarding import api as onboarding_api


@pytest.mark.parametrize(
    "vendor,mgmt_interface,expected_snippet",
    [
        ("cisco_ios", "GigabitEthernet0/0", "ip default-gateway 10.0.0.1"),
        ("cisco_xe", "GigabitEthernet0/0", "netconf-yang"),
        ("huawei_vrp", "GigabitEthernet0/0/0", "stelnet server enable"),
        ("juniper_junos", "fxp0", "set system services ssh"),
    ],
)
def test_day0_render_templates_for_all_vendors(vendor, mgmt_interface, expected_snippet):
    renderer = TemplateRenderer()
    params = {
        "hostname": "R1",
        "mgmt_ip": "10.0.0.10",
        "subnet_mask": "255.255.255.0",
        "mgmt_prefix_len": 24,
        "gateway": "10.0.0.1",
        "mgmt_interface": mgmt_interface,
        "vendor": vendor,
        "ssh_user": "admin",
        "ssh_password": "password123",
        "port": 22,
    }

    rendered = renderer.render(vendor, "day-0", params)

    assert "R1" in rendered
    assert expected_snippet in rendered


def test_day0_undo_templates_exist_for_all_vendors():
    renderer = TemplateRenderer()
    common_params = {
        "hostname": "R1",
        "mgmt_ip": "10.0.0.10",
        "subnet_mask": "255.255.255.0",
        "mgmt_prefix_len": 24,
        "gateway": "10.0.0.1",
        "ssh_user": "admin",
        "ssh_password": "password123",
        "port": 22,
    }
    interfaces = {
        "cisco_ios": "GigabitEthernet0/0",
        "cisco_xe": "GigabitEthernet0/0",
        "huawei_vrp": "GigabitEthernet0/0/0",
        "juniper_junos": "fxp0",
    }

    for vendor, mgmt_interface in interfaces.items():
        params = dict(common_params)
        params["vendor"] = vendor
        params["mgmt_interface"] = mgmt_interface

        undo = renderer.render_undo(vendor, "day-0", params)
        assert undo
        assert "admin" in undo


def test_renderer_list_templates_includes_day0_for_cisco_xe():
    renderer = TemplateRenderer()
    templates = renderer.list_templates("cisco_xe")

    day0 = [item for item in templates if item["feature"] == "day-0"]
    assert day0, "Expected cisco_xe day-0 template in template discovery"
    assert day0[0]["has_undo"] is True


def test_normalize_day0_params_adds_prefix_len():
    raw = {
        "hostname": "SW1",
        "mgmt_ip": "192.168.10.20",
        "subnet_mask": "255.255.255.0",
        "gateway": "192.168.10.1",
        "mgmt_interface": "GigabitEthernet0/0",
        "vendor": "cisco_ios",
        "ssh_user": "admin",
        "ssh_password": "pw",
        "port": 22,
    }

    normalized = onboarding_api._normalize_day0_params(raw)

    assert normalized["mgmt_prefix_len"] == 24


def test_resolve_gateway_prefers_access_device_gateway_value():
    device = SimpleNamespace(role="access", oob_gateway_ip="192.168.10.254", management_ip="192.168.1.48")

    assert onboarding_api._resolve_gateway(device) == "192.168.10.254"


def test_resolve_gateway_falls_back_to_access_management_subnet_dot_one():
    device = SimpleNamespace(role="access", oob_gateway_ip="", management_ip="192.168.1.48")

    assert onboarding_api._resolve_gateway(device) == "192.168.1.1"


def test_resolve_gateway_is_empty_for_non_access_roles():
    device = SimpleNamespace(role="distribution", oob_gateway_ip="192.168.10.254", management_ip="192.168.1.48")

    assert onboarding_api._resolve_gateway(device) == ""


def test_cisco_day0_enables_registered_discovery_interfaces():
    rendered = TemplateRenderer().render("cisco_ios", "day-0", {
        "hostname": "SW1",
        "mgmt_ip": "192.168.1.43",
        "subnet_mask": "255.255.255.0",
        "gateway": "192.168.1.1",
        "mgmt_interface": "Vlan1",
        "vendor": "cisco_ios",
        "role": "access",
        "ssh_user": "admin",
        "ssh_password": "pw",
        "port": 22,
        "discovery_interfaces": ["Ethernet0/1"],
    })

    assert "interface Ethernet0/1" in rendered
    assert " no shutdown" in rendered
    assert " lldp transmit" in rendered
    assert " cdp enable" in rendered


def test_render_day0_config_falls_back_to_legacy_generator(monkeypatch):
    class _BrokenRenderer:
        def render(self, vendor, feature, params):
            raise RuntimeError("forced failure")

    monkeypatch.setattr(onboarding_api, "_renderer", _BrokenRenderer())

    params = {
        "hostname": "R1",
        "mgmt_ip": "10.0.0.10",
        "subnet_mask": "255.255.255.0",
        "gateway": "10.0.0.1",
        "mgmt_interface": "GigabitEthernet0/0",
        "vendor": "cisco_xe",
        "ssh_user": "admin",
        "ssh_password": "password123",
        "port": 22,
    }

    rendered = onboarding_api._render_day0_config(onboarding_api._normalize_day0_params(params))

    assert "netconf-yang" in rendered
