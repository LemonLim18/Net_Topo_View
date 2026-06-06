"""Tests for planned-inventory cross-check of interface names."""

from ansible.context_entity_validation import (
    collect_user_interface_names_from_params,
    enrich_params_disambiguation_trunk_vlan_controls,
    explicit_interface_names_from_request_params,
    hosts_for_device_hint,
    intent_requests_all_interfaces,
    interface_names_for_host,
    intersection_interfaces_from_disambiguation_devices,
    normalize_interface_name,
    should_validate_interface_names_against_inventory,
    shutdown_list_is_placeholder_ethernet0_not_in_inventory,
    split_qualified_interface_token,
    validate_interface_params_against_context,
)


def test_normalize_interface_name_casefold_whitespace():
    assert normalize_interface_name("  Ethernet0/1  ") == normalize_interface_name("ethernet0/1")
    assert normalize_interface_name("Gi  0/1") == "gi 0/1".casefold()


def test_shutdown_placeholder_ethernet0_not_on_huawei_inventory():
    inv = {
        normalize_interface_name("GigabitEthernet1/0/1"),
        normalize_interface_name("GigabitEthernet1/0/2"),
    }
    llm_guess = [f"Ethernet0/{i}" for i in range(1, 25)]
    assert shutdown_list_is_placeholder_ethernet0_not_in_inventory(llm_guess, inv) is True


def test_shutdown_placeholder_not_triggered_when_ethernet0_in_inventory():
    inv = {normalize_interface_name("Ethernet0/1"), normalize_interface_name("Ethernet0/2")}
    assert shutdown_list_is_placeholder_ethernet0_not_in_inventory(["Ethernet0/1", "Ethernet0/2"], inv) is False


def test_shutdown_placeholder_not_triggered_for_mixed_or_short_lists():
    inv = {normalize_interface_name("GigabitEthernet1/0/1")}
    assert shutdown_list_is_placeholder_ethernet0_not_in_inventory(["Ethernet0/1"], inv) is False
    assert (
        shutdown_list_is_placeholder_ethernet0_not_in_inventory(
            ["Ethernet0/1", "GigabitEthernet1/0/1"], inv
        )
        is False
    )


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


def test_explicit_interface_names_from_nested_secondary_intents():
    """Studio may send interface picks only under _secondary_intents[].parameters.interfaces."""
    params = {
        "_secondary_intents": [
            {
                "action": "configure_interface",
                "feature": "interface",
                "parameters": {
                    "interfaces": [{"name": "Ethernet0/2", "mode": "access", "access_vlan": 30}],
                },
            }
        ]
    }
    names = explicit_interface_names_from_request_params(params)
    assert names == ["Ethernet0/2"]


def test_validate_interface_names_override_ignores_stale_llm_secondary():
    """req.params.interfaces is authoritative: do not fail on LLM Gi token in secondary_intents."""
    ctx = {
        "devices": [
            {
                "hostname": "Acc_Cisco_1",
                "interfaces": [{"name": "Ethernet0/2"}],
            }
        ]
    }
    params = {
        "interfaces": [{"name": "Ethernet0/2", "mode": "access"}],
    }
    stale_secondary = [
        {
            "action": "configure_interface",
            "feature": "interface",
            "parameters": {"interfaces": [{"name": "GigabitEthernet1/0/1", "mode": "access"}]},
        }
    ]
    override = explicit_interface_names_from_request_params(params)
    r = validate_interface_params_against_context(
        params,
        ["Acc_Cisco_1"],
        ctx,
        secondary_intents=stale_secondary,
        interface_names_override=override,
    )
    assert r.ok
    # Without override, collect() sees both names and fails on Gi.
    r2 = validate_interface_params_against_context(
        params,
        ["Acc_Cisco_1"],
        ctx,
        secondary_intents=stale_secondary,
    )
    assert not r2.ok


def test_explicit_interface_names_apply_to_is_authoritative_over_interfaces():
    """When apply_to exists, stale interfaces[] must be ignored."""
    params = {
        "apply_to": ["Acc_Cisco_1:Ethernet0/1 in"],
        "interfaces": [{"name": "GigabitEthernet1/0/1"}],  # stale / invalid
    }
    names = explicit_interface_names_from_request_params(params)
    assert len(names) == 1
    assert "Ethernet0/1" in names[0]
    assert "GigabitEthernet1/0/1" not in names


def test_validate_host_with_empty_interface_inventory_is_flagged():
    """Device present but no interface rows: names cannot be verified (not a silent pass)."""
    ctx = {
        "devices": [
            {"hostname": "sw1", "interfaces": []},
        ]
    }
    params = {"interfaces_to_shutdown": ["Gi0/1"]}
    r = validate_interface_params_against_context(params, ["sw1"], ctx)
    assert not r.ok


def test_intent_requests_all_interfaces():
    assert intent_requests_all_interfaces("Admin-down all interfaces on SW1")
    assert intent_requests_all_interfaces("Now turn on all the interfaces of SW1")
    assert intent_requests_all_interfaces("Enable all interfaces on SW1")
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
    assert should_validate_interface_names_against_inventory(
        "configure_ospf_passive_interface",
        "ospf",
        {"passive_interfaces": ["GigabitEthernet1/0/1"]},
    )
    assert not should_validate_interface_names_against_inventory(
        "shutdown_interface", "interface", {}
    )


def test_enrich_vlan_controls_applies_trunk_access_native():
    pd = {
        "question": "Please provide the following.",
        "fields": [
            {"field": "trunk_vlans", "question": "trunk?"},
            {"field": "access_vlan", "question": "access?"},
            {"field": "native_vlan", "question": "native?"},
        ],
    }
    parsed = {"parameters": {"vlan_id": 100}}
    out = enrich_params_disambiguation_trunk_vlan_controls(
        pd,
        planning_context=None,
        parsed_intent=parsed,
    )
    assert out is not None
    fmap = {str(f.get("field") or ""): f for f in (out.get("fields") or []) if isinstance(f, dict)}
    assert fmap["trunk_vlans"].get("control") == "multi_select_integers"
    assert fmap["access_vlan"].get("control") == "single_select_integers"
    assert fmap["native_vlan"].get("control") == "single_select_integers"
    assert fmap["trunk_vlans"].get("options") == [100]
    assert fmap["access_vlan"].get("options") == [100]
    assert fmap["native_vlan"].get("options") == [100]


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
    test_explicit_interface_names_from_nested_secondary_intents()
    test_validate_interface_names_override_ignores_stale_llm_secondary()
    test_validate_host_with_empty_interface_inventory_is_flagged()
    test_intent_requests_all_interfaces()
    test_intersection_interfaces_from_disambiguation_devices()
    test_interface_intent_satisfied_with_nettopo_shutdown_flag()
    test_should_validate_shutdown_and_vlan()
    print("test_context_entity_validation: all passed")
