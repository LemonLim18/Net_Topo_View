"""Tests for planned-inventory cross-check of interface names."""

from ansible.context_entity_validation import (
    collect_user_interface_names_from_params,
    hosts_for_device_hint,
    intent_requests_all_interfaces,
    interface_names_for_host,
    intersection_interfaces_from_disambiguation_devices,
    normalize_interface_name,
    should_validate_interface_names_against_inventory,
    split_qualified_interface_token,
    validate_interface_params_against_context,
)


def test_normalize_interface_name_casefold_whitespace():
    assert normalize_interface_name("  Ethernet0/1  ") == normalize_interface_name("ethernet0/1")
    assert normalize_interface_name("Gi  0/1") == "gi 0/1".casefold()


def test_collect_user_interface_names_order_unique():
    p = {
        "interfaces_to_shutdown": ["Gi0/1", "gi0/1", "Eth0/2"],
        "interfaces": [{"name": "Vlan1"}],
    }
    out = collect_user_interface_names_from_params(p)
    assert out[0] == "Gi0/1"
    assert "gi0/1" not in out[1:]  # deduped by normalized key
    assert "Eth0/2" in out
    assert "Vlan1" in out


def test_interface_names_for_host():
    ctx = {
        "devices": [
            {
                "hostname": "sw1",
                "interfaces": [
                    {"name": "Ethernet0/1"},
                    {"interface": "Vlan10"},
                ],
            }
        ]
    }
    assert set(interface_names_for_host(ctx, "sw1")) == {"Ethernet0/1", "Vlan10"}
    assert interface_names_for_host(ctx, "missing") == []


def test_split_qualified_interface_token():
    assert split_qualified_interface_token("SW1:Ethernet0/1") == ("SW1", "Ethernet0/1")
    assert split_qualified_interface_token("Ethernet0/1") == (None, "Ethernet0/1")


def test_qualified_name_validates_only_mapped_host_not_all_targets():
    """SW1:Ethernet0/1 must not produce errors on every host in resolved_hosts."""
    ctx = {
        "devices": [
            {
                "hostname": "Acc_Cisco_1",
                "role": "access",
                "interfaces": [{"name": "Ethernet0/1"}, {"name": "Ethernet1/0"}],
            },
            {
                "hostname": "Core_Cisco_1",
                "role": "core",
                "interfaces": [{"name": "GigabitEthernet1"}],
            },
        ]
    }
    params = {"interfaces_to_shutdown": ["SW1:Ethernet0/1", "SW1:Ethernet1/0"]}
    resolved = ["Acc_Cisco_1", "Core_Cisco_1"]
    r = validate_interface_params_against_context(params, resolved, ctx)
    assert r.ok


def test_ambiguous_sw1_returns_device_hint_error_not_per_host_spam():
    """Two access devices both ending in _1 → SW1 cannot map uniquely."""
    ctx = {
        "devices": [
            {"hostname": "Acc_Cisco_1", "role": "access", "interfaces": [{"name": "Ethernet0/1"}]},
            {"hostname": "Lab_Access_1", "role": "access", "interfaces": [{"name": "Ethernet0/1"}]},
        ]
    }
    params = {"interfaces_to_shutdown": ["SW1:Ethernet0/1"]}
    r = validate_interface_params_against_context(
        params, ["Acc_Cisco_1", "Lab_Access_1"], ctx
    )
    assert not r.ok
    kinds = {e["kind"] for e in r.invalid_entities}
    assert "device_hint" in kinds
    assert not any(e["kind"] == "interface" for e in r.invalid_entities)


def test_hosts_for_device_hint_exact():
    assert hosts_for_device_hint("Acc_Cisco_1", ["Acc_Cisco_1", "x"], None) == ["Acc_Cisco_1"]


def test_validate_mismatch_gigabit_vs_ethernet():
    ctx = {
        "devices": [
            {
                "hostname": "Acc_Cisco_1",
                "interfaces": [{"name": "Ethernet0/1"}],
            }
        ]
    }
    params = {"interfaces_to_shutdown": ["GigabitEthernet0/1"]}
    r = validate_interface_params_against_context(
        params, ["Acc_Cisco_1"], ctx
    )
    assert not r.ok
    assert len(r.invalid_entities) >= 1
    assert r.invalid_entities[0]["value"] == "GigabitEthernet0/1"
    assert "Acc_Cisco_1" in r.invalid_entities[0]["hostname"]


def test_validate_match_case_insensitive():
    ctx = {
        "devices": [
            {
                "hostname": "sw1",
                "interfaces": [{"name": "Ethernet0/1"}],
            }
        ]
    }
    params = {"interfaces_to_shutdown": ["ethernet0/1"]}
    r = validate_interface_params_against_context(params, ["sw1"], ctx)
    assert r.ok


def test_validate_skips_host_with_no_interface_inventory():
    """Cannot prove invalid when inventory has no interfaces for that host."""
    ctx = {
        "devices": [
            {"hostname": "sw1", "interfaces": []},
        ]
    }
    params = {"interfaces_to_shutdown": ["Gi0/1"]}
    r = validate_interface_params_against_context(params, ["sw1"], ctx)
    assert r.ok


def test_intent_requests_all_interfaces():
    assert intent_requests_all_interfaces("Admin-down all interfaces on SW1")
    assert intent_requests_all_interfaces("shut down all ports")
    assert intent_requests_all_interfaces("shutdown all")
    assert not intent_requests_all_interfaces("shutdown Ethernet0/1")


def test_intersection_interfaces_from_disambiguation_devices():
    devices = [
        {"hostname": "a", "interfaces": [{"name": "Gi1"}, {"name": "Gi2"}]},
        {"hostname": "b", "interfaces": [{"name": "Gi1"}, {"name": "Gi2"}, {"name": "Gi3"}]},
    ]
    assert intersection_interfaces_from_disambiguation_devices(devices) == ["Gi1", "Gi2"]
    assert intersection_interfaces_from_disambiguation_devices(
        [
            {"hostname": "a", "interfaces": [{"name": "Gi1"}]},
            {"hostname": "b", "interfaces": [{"name": "Gi2"}]},
        ]
    ) == []


def test_interface_intent_satisfied_with_nettopo_shutdown_flag():
    from ansible.intent_validation import interface_intent_params_satisfied

    assert interface_intent_params_satisfied({"interfaces_shutdown_use_nettopo": True})


def test_should_validate_shutdown_and_vlan():
    assert should_validate_interface_names_against_inventory(
        "shutdown_interface", "interface", {"interfaces_to_shutdown": ["Gi0/1"]}
    )
    assert should_validate_interface_names_against_inventory(
        "create_vlan", "vlan", {"interfaces": [{"name": "Gi0/1"}]}
    )
    assert not should_validate_interface_names_against_inventory(
        "shutdown_interface", "interface", {}
    )


if __name__ == "__main__":
    test_normalize_interface_name_casefold_whitespace()
    test_collect_user_interface_names_order_unique()
    test_split_qualified_interface_token()
    test_interface_names_for_host()
    test_qualified_name_validates_only_mapped_host_not_all_targets()
    test_ambiguous_sw1_returns_device_hint_error_not_per_host_spam()
    test_hosts_for_device_hint_exact()
    test_validate_mismatch_gigabit_vs_ethernet()
    test_validate_match_case_insensitive()
    test_validate_skips_host_with_no_interface_inventory()
    test_intent_requests_all_interfaces()
    test_intersection_interfaces_from_disambiguation_devices()
    test_interface_intent_satisfied_with_nettopo_shutdown_flag()
    test_should_validate_shutdown_and_vlan()
    print("test_context_entity_validation: all passed")
