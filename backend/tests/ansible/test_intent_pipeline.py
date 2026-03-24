"""Unit tests for refactored intent pipeline (no LLM)."""

from ansible.intent_pipeline.cross_check import cross_check_parsed_intent
from ansible.intent_validation import correct_vlan_interface_binding_from_trunk_prompt
from ansible.intent_pipeline.intent_splitter import split_multi_intent
from ansible.intent_pipeline.operation_adapter import apply_operation_adapter
from ansible.intent_pipeline.orchestrator import merge_multi_intent_llm_response, prepare_llm_input_dict
from ansible.intent_pipeline.operation_adapter import apply_operation_adapter
from ansible.intent_pipeline.orchestrator import build_pipeline_trace
from ansible.intent_pipeline.schema_loader import reload_catalog
from ansible.intent_pipeline.validator import validate_llm_intent_dict
from llm.intent import (
    enumerate_layer_switch_device_candidates,
    extract_layer_switch_role_and_numbers,
    parse_vlan_database_params_from_text,
)
from ansible.api import (
    _extract_non_exact_device_aliases_from_prompt,
    _is_management_interface,
    _normalize_mismatched_qualified_interface_tokens,
    _normalize_secondary_intents_qualified_interface_tokens,
    _resolve_unknown_device_aliases,
)
from ansible.context_entity_validation import build_planned_all_interfaces_preview
from ansible.intent_validation import (
    CHAT_INTERFACE_LAYER_FIELD,
    CHAT_INTERFACE_L2_MODE_FIELD,
    apply_chat_interface_wizard_to_parameters,
    configure_interface_conditional_disambiguation_fields,
    configure_interface_phased_wizard_fields,
    configure_interface_prompt_is_layer_ambiguous,
    correct_interface_l3_params_from_natural_language,
)
from ansible.api import _coerce_params_for_validation
from ansible.intent_validation import (
    get_schema_for_parsed_intent,
    validate_completeness,
)
from ansible.intent_validation import merge_prompt_aware_disambiguation_for_schema
from ansible.reference_resolver import fill_playbook_from_reference


def test_parse_vlan_database_params_from_text():
    p = parse_vlan_database_params_from_text(
        "Create VLAN 67 named ShuYing on access switch 1, 2, and 3"
    )
    assert p["vlans"][0]["id"] == 67
    assert p["vlans"][0]["name"] == "ShuYing"
    p2 = parse_vlan_database_params_from_text("Create VLAN 120 named WEB_Vlan on SW1 and SW2")
    assert p2["vlans"][0]["id"] == 120
    assert p2["vlans"][0]["name"] == "WEB_Vlan"


def test_layer_switch_numbers_and_candidates():
    assert extract_layer_switch_role_and_numbers("on access switch 1, 2, and 3") == ("access", [1, 2, 3])
    inv = [
        {"hostname": "Acc_Cisco_1", "role": "access"},
        {"hostname": "Acc_Cisco_2", "role": "access"},
        {"hostname": "Acc_Cisco_5", "role": "access"},
    ]
    c = enumerate_layer_switch_device_candidates(
        "Create VLAN 10 on access switch 1, 2", inv
    )
    assert "Acc_Cisco_1" in c and "Acc_Cisco_2" in c
    assert "Acc_Cisco_5" not in c


def test_pipeline_trace_device_candidate_ask_suppresses_missing_targets():
    reload_catalog()
    flat = {
        "action": "create_vlan",
        "feature": "vlan",
        "parameters": {
            "vlans": [{"id": 10, "name": "A"}],
            "_target_device_candidates": ["Acc_Cisco_1", "Acc_Cisco_2"],
            "_target_device_candidate_reason": "test",
        },
        "scope": {"device_hostnames": []},
        "resolved_devices": [],
    }
    trace = build_pipeline_trace("x", flat, None)
    kinds = [a.get("kind") for a in trace["interactive_asks"]]
    assert "select_planned_devices" in kinds
    assert trace["catalog_validation_ok"] is True
    assert "target_devices" not in trace["catalog_missing_fields"]


def test_split_multi_intent_single():
    assert split_multi_intent("Create VLAN 10 on SW1") == ["Create VLAN 10 on SW1"]


def test_split_multi_intent_and_configure():
    s = (
        "Create VLAN 100 named HR and configure GigabitEthernet1/0/1 as access VLAN 100 on SW1"
    )
    parts = split_multi_intent(s)
    assert len(parts) >= 2
    assert "VLAN 100" in parts[0]


def test_split_semicolon():
    s = "Add default route via 192.168.1.1; enable OSPF process 1 on SW1"
    parts = split_multi_intent(s)
    assert len(parts) == 2


def test_merge_multi_intent_llm_response():
    raw = {
        "intents": [
            {
                "intent_type": "configure",
                "action": "create_vlan",
                "feature": "vlan",
                "parameters": {"vlan_id": 10, "vlan_name": "a"},
                "scope": {"device_hostnames": ["SW1"], "all_devices": False},
            },
            {
                "intent_type": "configure",
                "action": "configure_interface",
                "feature": "interface",
                "parameters": {"interfaces": [{"name": "Gi1/0/1", "mode": "access"}]},
                "scope": {"device_hostnames": ["SW1"], "all_devices": False},
            },
        ]
    }
    merged = merge_multi_intent_llm_response(raw)
    assert merged.get("action") == "create_vlan"
    sec = merged.get("secondary_intents") or []
    assert len(sec) == 1
    assert sec[0].get("action") == "configure_interface"


def test_prepare_llm_input_dict_pass_through():
    d = {"action": "create_vlan", "feature": "vlan", "parameters": {}}
    assert prepare_llm_input_dict(d) == d


def test_validate_create_vlan_vlans_and_targets():
    ok, miss = validate_llm_intent_dict(
        {
            "action": "create_vlan",
            "feature": "vlan",
            "resolved_devices": ["Acc_Cisco_1"],
            "parameters": {"vlans": [{"id": 67, "name": "ShuYing"}]},
            "scope": {},
        }
    )
    assert ok is True
    assert miss == []


def test_validate_create_vlan_legacy_vlan_id_still_ok():
    ok, miss = validate_llm_intent_dict(
        {
            "action": "create_vlan",
            "feature": "vlan",
            "resolved_devices": ["H1"],
            "parameters": {"vlan_id": 10, "vlan_name": "A"},
            "scope": {},
        }
    )
    assert ok is True
    assert miss == []


def test_configure_acl_apply_group_satisfied():
    reload_catalog()
    ok, miss = validate_llm_intent_dict(
        {
            "action": "configure_acl",
            "feature": "acl",
            "parameters": {
                "acl_name": "3000",
                "interfaces": [{"name": "Gi1/0/1", "direction": "inbound"}],
                "target_devices": ["SW1"],
            },
        }
    )
    assert ok is True
    assert miss == []


def test_configure_acl_both_groups_incomplete():
    reload_catalog()
    ok, miss = validate_llm_intent_dict(
        {
            "action": "configure_acl",
            "feature": "acl",
            "parameters": {
                "acl_name": "2000",
                "target_devices": ["SW1"],
            },
        }
    )
    assert ok is False
    assert miss


def test_validation_uses_canonical_operation_after_adapter():
    reload_catalog()
    frag = apply_operation_adapter(
        {
            "action": "apply_acl_to_interface",
            "feature": "acl",
            "parameters": {
                "acl_name": "BLOCK",
                "interfaces": [{"name": "Gi0/1", "direction": "inbound"}],
                "target_devices": ["SW1"],
            },
        }
    )
    ok, miss = validate_llm_intent_dict(frag)
    assert ok is True
    assert miss == []


def test_validate_create_vlan_missing_targets_when_unresolved():
    ok, miss = validate_llm_intent_dict(
        {
            "action": "create_vlan",
            "feature": "vlan",
            "resolved_devices": [],
            "parameters": {"vlans": [{"id": 10, "name": "A"}]},
            "scope": {},
        }
    )
    assert ok is False
    assert "target_devices" in miss


def test_operation_adapter_action_key_without_canonical():
    frag = {
        "action": "assign_vlan_to_trunk_port",
        "feature": "interface",
        "parameters": {"interfaces": [{"name": "Gi0/1", "mode": "trunk", "trunk_vlans": [10]}]},
    }
    out = apply_operation_adapter(frag)
    assert out.get("action") == "configure_interface"


def test_operation_adapter_canonical():
    frag = {
        "canonical_operation": "assign_vlan_to_access_port",
        "action": "unknown",
        "feature": "",
        "parameters": {"vlan_id": 30, "target_interfaces": [{"name": "Gi0/1"}]},
    }
    out = apply_operation_adapter(frag)
    assert out.get("action") == "configure_interface"
    assert "interfaces" in (out.get("parameters") or {})


def test_cross_check_no_db():
    data = {
        "scope": {"device_hostnames": ["Ghost"], "all_devices": False},
        "resolved_devices": [],
        "parameters": {},
    }
    r = cross_check_parsed_intent(data, None)
    assert r["invalid_devices"] == []


class _FakeDev:
    def __init__(self, dev_id: int, hostname: str):
        self.id = dev_id
        self.hostname = hostname


class _FakeIface:
    def __init__(self, name: str):
        self.name = name


class _FakeDbManager:
    """Minimal stub for cross_check_parsed_intent (inventory + interfaces)."""

    def __init__(self):
        self._devices = [_FakeDev(1, "Acc_Cisco_1")]
        self._ifaces = {
            1: [
                _FakeIface("GigabitEthernet0/0/1"),
                _FakeIface("FastEthernet0/1"),
            ],
        }

    def get_all_devices(self):
        return self._devices

    def get_interfaces_for_device(self, dev_id: int):
        return list(self._ifaces.get(dev_id, []))


def test_cross_check_suppresses_narrative_scope_host_when_resolved_valid():
    """SW1 in scope only must not invalidate device pick once real inventory hosts are resolved."""
    db = _FakeDbManager()
    data = {
        "scope": {"device_hostnames": ["SW1"], "all_devices": False},
        "resolved_devices": ["Acc_Cisco_1"],
        "parameters": {
            "vlan_id": 30,
            "interfaces": ["GigabitEthernet1/0/1"],
        },
    }
    r = cross_check_parsed_intent(data, db)
    assert r["invalid_devices"] == []
    assert len(r["invalid_interfaces"]) == 1
    row = r["invalid_interfaces"][0]
    assert row["hostname"] == "Acc_Cisco_1"
    assert row["value"] == "GigabitEthernet1/0/1"
    assert set(row["suggestions"]) == {"GigabitEthernet0/0/1", "FastEthernet0/1"}


def test_trunk_prompt_coerces_access_shaped_vlan_binding():
    """User says trunk port; LLM must not leave access + access_vlan for allowed-VLAN-on-trunk."""
    pi = {
        "parameters": {
            "vlan_id": 10,
            "vlan_name": "Jason",
            "interfaces": [
                {"name": "Acc_Cisco_1:Ethernet0/2", "mode": "access", "access_vlan": 10},
            ],
        },
    }
    correct_vlan_interface_binding_from_trunk_prompt(
        "Create VLAN 10 and allow it on trunk port GigabitEthernet1/0/2 on SW1",
        pi,
    )
    iface = pi["parameters"]["interfaces"][0]
    assert iface.get("mode") == "trunk"
    assert iface.get("trunk_vlans") == [10]
    assert "access_vlan" not in iface


def test_allow_vlan_on_port_phrase_coerces_to_trunk():
    """Generic allow-on-port wording should map to trunk allowed VLAN semantics."""
    pi = {
        "parameters": {
            "vlan_id": 10,
            "interfaces": [
                {"name": "Acc_Cisco_1:Ethernet0/3", "mode": "access", "access_vlan": 10},
            ],
        },
    }
    correct_vlan_interface_binding_from_trunk_prompt(
        "Create VLAN 10 and allow it on port Ethernet0/3 on SW1",
        pi,
    )
    iface = pi["parameters"]["interfaces"][0]
    assert iface.get("mode") == "trunk"
    assert iface.get("trunk_vlans") == [10]
    assert "access_vlan" not in iface


def test_trunk_prompt_coerces_interface_binding_key():
    """interface_binding (create_vlan.yml) must also be normalized to trunk."""
    pi = {
        "parameters": {
            "vlan_id": 10,
            "vlan_name": "shenao",
            "interface_binding": [
                {"name": "Acc_Cisco_1:Ethernet0/1", "mode": "access", "access_vlan": 10},
            ],
        },
    }
    correct_vlan_interface_binding_from_trunk_prompt(
        "Create VLAN 10 and allow it on trunk port Ethernet0/1 on SW1",
        pi,
    )
    iface = pi["parameters"]["interface_binding"][0]
    assert iface.get("mode") == "trunk"
    assert iface.get("trunk_vlans") == [10]
    assert "access_vlan" not in iface


def test_intent_requests_all_interfaces_detection_interface_shutdown():
    """Sanity check for wildcard detection used to avoid free-text interfaces_to_shutdown asks."""
    from ansible.context_entity_validation import intent_requests_all_interfaces

    assert intent_requests_all_interfaces("Admin-down all interfaces on SW1") is True
    assert intent_requests_all_interfaces("shutdown all") is True
    assert intent_requests_all_interfaces("shutdown Ethernet0/1") is False


def test_cross_check_no_invalid_devices_when_resolved_valid_even_if_params_repeat_sw1():
    """LLM may echo SW1 in parameters; valid resolved_devices are authoritative."""
    db = _FakeDbManager()
    data = {
        "scope": {"device_hostnames": ["SW1"], "all_devices": False},
        "resolved_devices": ["Acc_Cisco_1"],
        "parameters": {"target_hosts": ["SW1", "Acc_Cisco_1"]},
    }
    r = cross_check_parsed_intent(data, db)
    assert r["invalid_devices"] == []


def test_doc_example_prompts_split_or_single():
    prompts = [
        "Create VLAN 67 named ShuYing on access switch 1, 2, and 3",
        "Configure GigabitEthernet1/0/1 as access port with VLAN 30 on SW1",
        "Add ACL 2000 to permit SSH from 192.168.1.0/24 on SW1",
        "Add a default route via 192.168.1.1 on SW1",
        (
            "Enable OSPF process 1, set router ID 1.1.1.1, "
            "and advertise network 10.0.0.0/24 in area 0 on SW1"
        ),
    ]
    for p in prompts:
        segs = split_multi_intent(p)
        assert len(segs) >= 1
        assert all(len(x.strip()) > 0 for x in segs)


class _AliasDev:
    def __init__(self, hostname: str, role: str = "access"):
        self.hostname = hostname
        self.role = role


class _AliasDb:
    def __init__(self):
        self._devices = [
            _AliasDev("Acc_Cisco_1", "access"),
            _AliasDev("Acc_Cisco_2", "access"),
            _AliasDev("Acc_Huawei_3", "access"),
            _AliasDev("Core_Cisco_1", "core"),
        ]

    def get_all_devices(self):
        return list(self._devices)


def test_unknown_alias_router2_requires_user_selection_instead_of_auto_map():
    """
    Non-exact aliases (e.g. "router 2") must not silently map to a canonical hostname.
    Backend should always return disambiguation with closest + others sections.
    """
    auto, dd = _resolve_unknown_device_aliases(["router 2"], _AliasDb())
    assert auto == []
    assert isinstance(dd, dict)
    assert dd.get("ask_user_selection") is True
    closest = dd.get("closest_candidates") or []
    others = dd.get("other_candidates") or []
    assert "Acc_Cisco_2" in closest
    assert "Core_Cisco_1" not in closest
    assert "Core_Cisco_1" in others


def test_extract_non_exact_device_aliases_detects_router_phrase():
    aliases = _extract_non_exact_device_aliases_from_prompt(
        "Shut down GigabitEthernet1/0/1 on router 2",
        _AliasDb(),
    )
    assert "router 2" in [a.lower() for a in aliases]


def test_normalize_mismatched_qualified_interface_tokens_strips_stale_device_hint():
    params = {"interfaces_to_shutdown": ["Dist_Cisco_1:Ethernet1/0/1"]}
    out = _normalize_mismatched_qualified_interface_tokens(params, ["Acc_Cisco_2"])
    assert out["interfaces_to_shutdown"] == ["Ethernet1/0/1"]


def test_normalize_secondary_intents_strips_stale_device_hint():
    sec = [
        {
            "action": "shutdown_interface",
            "parameters": {"interfaces_to_shutdown": ["Dist_Cisco_1:Ethernet1/0/1"]},
        }
    ]
    out = _normalize_secondary_intents_qualified_interface_tokens(sec, ["Acc_Cisco_2"])
    assert out[0]["parameters"]["interfaces_to_shutdown"] == ["Ethernet1/0/1"]


def test_planned_all_interfaces_preview_excludes_management_and_has_no_note():
    planning_context = {
        "devices": [
            {
                "hostname": "Acc_Cisco_2",
                "management_ip": "10.0.0.2",
                "interfaces": [
                    {"name": "Management0/0", "ip_address": "10.0.0.2"},
                    {"name": "Ethernet0/1", "ip_address": "192.168.1.1"},
                    {"name": "Ethernet0/2", "ip_address": "10.0.0.2"},
                    {"name": "Console0"},
                ],
            }
        ]
    }
    out = build_planned_all_interfaces_preview(planning_context, ["Acc_Cisco_2"])
    names = [str(i.get("name") or "") for i in out["devices"][0]["interfaces"]]
    assert names == ["Ethernet0/1"]
    assert "note" not in out


def test_configure_interface_conditional_asks_access_vlan_for_access_ports():
    fields = configure_interface_conditional_disambiguation_fields(
        {
            "interfaces": [
                {"name": "Acc_Cisco_1:Ethernet0/2", "mode": "access"},
                {"name": "Acc_Cisco_1:Ethernet0/3", "mode": "access"},
            ]
        }
    )
    asked = {str(f.get("field") or "") for f in fields}
    assert "access_vlan" in asked


def test_configure_interface_conditional_string_iface_and_default_mode_routed_asks_ip():
    fields = configure_interface_conditional_disambiguation_fields(
        {
            "default_mode": "routed",
            "interfaces": ["Acc_Cisco_1:Ethernet0/3"],
        }
    )
    assert any(str(f.get("field") or "") == "ip_address" for f in fields)


def test_configure_interface_conditional_asks_ip_when_routed_without_ip():
    fields = configure_interface_conditional_disambiguation_fields(
        {
            "default_mode": "routed",
            "interfaces": [{"name": "Acc_Cisco_1:GigabitEthernet0/1", "mode": "routed"}],
        }
    )
    asked = {str(f.get("field") or "") for f in fields}
    assert "ip_address" in asked


def test_configure_interface_conditional_skips_ip_question_when_address_present():
    fields = configure_interface_conditional_disambiguation_fields(
        {
            "default_mode": "routed",
            "interfaces": [
                {
                    "name": "Acc_Cisco_1:GigabitEthernet0/1",
                    "mode": "routed",
                    "ip_address": "10.0.0.1",
                }
            ],
        }
    )
    asked = {str(f.get("field") or "") for f in fields}
    assert "ip_address" not in asked


def test_apply_chat_wizard_reconciles_string_interfaces_to_routed_rows():
    p = {
        CHAT_INTERFACE_LAYER_FIELD: "l3",
        "interfaces": ["Acc_Cisco_1:Ethernet0/3"],
    }
    apply_chat_interface_wizard_to_parameters(p)
    assert p.get("default_mode") == "routed"
    assert p["interfaces"] == [{"name": "Acc_Cisco_1:Ethernet0/3", "mode": "routed"}]


def test_coerce_propagates_top_level_ip_address_to_routed_rows():
    p = {
        "ip_address": "192.168.10.1/24",
        "interfaces": [{"name": "r1:Eth0", "mode": "routed"}],
    }
    _coerce_params_for_validation(p)
    row = p["interfaces"][0]
    assert row.get("ip_address") == "192.168.10.1"
    assert row.get("subnet_mask") == "255.255.255.0"


def test_coerce_propagates_top_level_native_vlan_to_trunk_rows():
    p = {
        "native_vlan": 99,
        "interfaces": [{"name": "sw:Gi0/1", "mode": "trunk", "trunk_vlans": [10, 20]}],
    }
    _coerce_params_for_validation(p)
    assert p["interfaces"][0].get("native_vlan") == 99


def test_configure_interface_conditional_asks_native_vlan_for_trunk_ports():
    fields = configure_interface_conditional_disambiguation_fields(
        {
            "interfaces": [
                {"name": "Acc_Cisco_1:Ethernet0/2", "mode": "trunk", "trunk_vlans": [10, 20]},
            ]
        }
    )
    native = next((f for f in fields if str(f.get("field") or "") == "native_vlan"), None)
    assert native is not None
    assert "optional" not in native


def test_is_management_interface_matches_nested_ip_dict_and_cidr():
    assert _is_management_interface(
        {"name": "Ethernet0/0", "ip": {"address": "10.0.0.1/24"}},
        "10.0.0.1",
    ) is True


def test_configure_interface_schema_allows_access_mode_without_access_vlan():
    intent = {
        "action": "configure_interface",
        "feature": "interface",
        "params": {
            "target_hosts": ["Acc_Cisco_1"],
            "interfaces": [{"name": "Ethernet0/2", "mode": "access"}],
        },
    }
    schema = get_schema_for_parsed_intent(intent)
    missing, errors = validate_completeness(intent, schema, action_or_feature="configure_interface")
    assert errors == []
    assert "access_vlan" not in missing


def test_configure_interface_schema_allows_trunk_mode_without_trunk_vlans():
    intent = {
        "action": "configure_interface",
        "feature": "interface",
        "params": {
            "target_hosts": ["Acc_Cisco_1"],
            "interfaces": [{"name": "Acc_Cisco_1:Ethernet0/1", "mode": "trunk"}],
        },
    }
    schema = get_schema_for_parsed_intent(intent)
    missing, errors = validate_completeness(intent, schema, action_or_feature="configure_interface")
    assert errors == []
    assert "trunk_vlans" not in missing
    assert "allowed_vlans" not in missing


def test_reference_fill_strips_qualified_interface_names_for_cisco():
    intent = {
        "target_hosts": ["Acc_Cisco_1"],
        "params": {
            "interfaces": [
                {
                    "name": "Acc_Cisco_1:Ethernet0/2",
                    "mode": "access",
                    "enabled": True,
                }
            ]
        },
    }
    rendered = fill_playbook_from_reference("interface", ["cisco_ios"], intent)
    assert isinstance(rendered, str) and rendered
    assert "Acc_Cisco_1:Ethernet0/2" not in rendered
    assert "Ethernet0/2" in rendered


def test_reference_fill_cisco_includes_access_mode_commands():
    intent = {
        "target_hosts": ["Acc_Cisco_1"],
        "params": {
            "interfaces": [
                {
                    "name": "Ethernet0/2",
                    "mode": "access",
                    "enabled": True,
                }
            ]
        },
    }
    rendered = fill_playbook_from_reference("interface", ["cisco_ios"], intent)
    assert isinstance(rendered, str) and rendered
    assert "switchport mode access" in rendered


def test_reference_fill_cisco_trunk_uses_add_semantics_and_dot1q():
    intent = {
        "target_hosts": ["Acc_Cisco_1"],
        "params": {
            "interfaces": [
                {
                    "name": "Ethernet1/0",
                    "mode": "trunk",
                    "trunk_vlans": [10, 20, 67],
                    "enabled": True,
                }
            ]
        },
    }
    rendered = fill_playbook_from_reference("interface", ["cisco_ios"], intent)
    assert isinstance(rendered, str) and rendered
    assert "switchport trunk encapsulation dot1q" in rendered
    assert "switchport trunk allowed vlan add" in rendered


def test_reference_fill_huawei_interface_renders_successfully():
    intent = {
        "target_hosts": ["Acc_Huawei_3"],
        "params": {
            "interfaces": [
                {
                    "name": "Acc_Huawei_3:Ethernet0/3",
                    "mode": "access",
                    "access_vlan": 10,
                    "enabled": True,
                }
            ]
        },
    }
    rendered = fill_playbook_from_reference("interface", ["huawei_vrp"], intent)
    assert isinstance(rendered, str) and rendered
    assert "community.network.ce_config" in rendered
    assert "port link-type access" in rendered
    # Must keep runtime loop expressions intact (first-pass fill must not collapse these).
    assert "replace('GigabitEthernet', 'GE')" in rendered
    assert 'loop: "{{ interfaces_to_shutdown | default([]) }}"' in rendered


def test_reference_fill_huawei_trunk_includes_native_vlan_command():
    intent = {
        "target_hosts": ["Acc_Huawei_3"],
        "params": {
            "interfaces": [
                {
                    "name": "GigabitEthernet1/0/3",
                    "mode": "trunk",
                    "trunk_vlans": [12, 13, 14],
                    "native_vlan": 12,
                    "enabled": True,
                }
            ]
        },
    }
    rendered = fill_playbook_from_reference("interface", ["huawei_vrp"], intent)
    assert isinstance(rendered, str) and rendered
    assert "port trunk allow-pass vlan" in rendered
    assert "port trunk pvid vlan" in rendered


def test_reference_fill_huawei_normalizes_long_interface_names_for_vrp():
    intent = {
        "target_hosts": ["Acc_Huawei_3"],
        "params": {
            "interfaces": [
                {
                    "name": "GigabitEthernet1/0/2",
                    "mode": "access",
                    "access_vlan": 124,
                    "enabled": True,
                }
            ]
        },
    }
    rendered = fill_playbook_from_reference("interface", ["huawei_vrp"], intent)
    assert isinstance(rendered, str) and rendered
    assert "replace('GigabitEthernet', 'GE')" in rendered
    assert "replace('TenGigabitEthernet', '10GE')" in rendered
    assert 'parents: "interface {{ (item.name | string | replace(' in rendered


def test_reference_fill_multi_vendor_interface_keeps_huawei_name_normalization():
    intent = {
        "target_hosts": ["Acc_Cisco_1", "Acc_Huawei_3"],
        "params": {
            "interfaces": [
                {
                    "name": "GigabitEthernet1/0/2",
                    "mode": "access",
                    "access_vlan": 124,
                    "enabled": True,
                }
            ]
        },
    }
    rendered = fill_playbook_from_reference("interface", ["cisco_ios", "huawei_vrp"], intent)
    assert isinstance(rendered, str) and rendered
    assert "inventory_hostname in groups.get('vendor_huawei_vrp', [])" in rendered
    assert "replace('GigabitEthernet', 'GE')" in rendered
    assert 'parents: "interface {{ item.name }}"' in rendered  # Cisco path unchanged


def test_reference_fill_huawei_vlan_keeps_interface_name_normalization():
    intent = {
        "target_hosts": ["Acc_Huawei_3"],
        "params": {
            "vlan_id": 124,
            "vlan_name": "VLAN124",
            "interfaces": [
                {
                    "name": "GigabitEthernet1/0/2",
                    "mode": "access",
                    "access_vlan": 124,
                }
            ],
        },
    }
    rendered = fill_playbook_from_reference("vlan", ["huawei_vrp"], intent)
    assert isinstance(rendered, str) and rendered
    assert "inventory_hostname in groups.get('vendor_huawei_vrp', [])" in rendered
    assert "parents: interface GE1/0/2" in rendered


def test_reference_fill_juniper_interface_renders_successfully():
    intent = {
        "target_hosts": ["Acc_Juniper_1"],
        "params": {
            "interfaces": [
                {
                    "name": "ge-0/0/1",
                    "enabled": True,
                }
            ]
        },
    }
    rendered = fill_playbook_from_reference("interface", ["juniper_junos"], intent)
    assert isinstance(rendered, str) and rendered
    assert "juniper.device.junos_interfaces" in rendered


def test_correct_interface_l3_coerces_routed_from_cidr_prompt():
    pi = {
        "action": "configure_interface",
        "feature": "interface",
        "parameters": {
            "interfaces": [{"name": "Acc_Cisco_1:Ethernet0/1", "mode": "access"}],
        },
    }
    correct_interface_l3_params_from_natural_language(
        "Set GigabitEthernet1/0/3 with IP address 192.168.10.1/24 on SW1",
        pi,
    )
    iface = pi["parameters"]["interfaces"][0]
    assert iface.get("mode") == "routed"
    assert iface.get("ip_address") == "192.168.10.1"
    assert iface.get("subnet_mask") == "255.255.255.0"
    # Inventory / UI-selected interface must not be replaced by the prompt's interface token.
    assert iface.get("name") == "Acc_Cisco_1:Ethernet0/1"


def test_correct_interface_l3_merges_into_routed_row_without_duplicating_prompt_iface_name():
    """Routed placeholder without IP must get L3 from NL without appending Gi* from the prompt."""
    pi = {
        "action": "configure_interface",
        "feature": "interface",
        "parameters": {
            "interfaces": [{"name": "Ethernet0/3", "mode": "routed"}],
        },
    }
    correct_interface_l3_params_from_natural_language(
        "Set GigabitEthernet1/0/3 with IP address 192.168.10.1/24 on Acc_Cisco_1",
        pi,
    )
    ifaces = pi["parameters"]["interfaces"]
    assert len(ifaces) == 1
    iface = ifaces[0]
    assert iface.get("name") == "Ethernet0/3"
    assert iface.get("mode") == "routed"
    assert iface.get("ip_address") == "192.168.10.1"
    assert iface.get("subnet_mask") == "255.255.255.0"


def test_correct_interface_l3_with_ip_only_keeps_missing_subnet_for_follow_up():
    pi = {
        "action": "configure_interface",
        "feature": "interface",
        "parameters": {
            "interfaces": [{"name": "Ethernet0/3", "mode": "access"}],
        },
    }
    correct_interface_l3_params_from_natural_language(
        "Set GigabitEthernet1/0/3 with IP address 192.168.10.1 on SW1",
        pi,
    )
    iface = pi["parameters"]["interfaces"][0]
    assert iface.get("name") == "Ethernet0/3"
    assert iface.get("mode") == "routed"
    assert iface.get("ip_address") == "192.168.10.1"
    assert iface.get("subnet_mask") in (None, "")


def test_configure_interface_vague_prompt_is_layer_ambiguous():
    assert configure_interface_prompt_is_layer_ambiguous(
        "Configure interfaces on devices",
        {},
    )
    assert not configure_interface_prompt_is_layer_ambiguous(
        "Configure interfaces on devices",
        {"default_mode": "access"},
    )


def test_configure_interface_phased_wizard_layer_then_l2_mode():
    fields0 = configure_interface_phased_wizard_fields(
        "Configure interfaces on devices",
        {},
        trusted_param_keys=set(),
    )
    assert len(fields0) == 1
    assert fields0[0].get("field") == CHAT_INTERFACE_LAYER_FIELD
    assert fields0[0].get("control") == "single_select_strings"

    fields1 = configure_interface_phased_wizard_fields(
        "Configure interfaces on devices",
        {CHAT_INTERFACE_LAYER_FIELD: "l2"},
        trusted_param_keys={CHAT_INTERFACE_LAYER_FIELD},
    )
    assert len(fields1) == 1
    assert fields1[0].get("field") == CHAT_INTERFACE_L2_MODE_FIELD

    fields2 = configure_interface_phased_wizard_fields(
        "Configure interfaces on devices",
        {
            CHAT_INTERFACE_LAYER_FIELD: "l2",
            CHAT_INTERFACE_L2_MODE_FIELD: "access",
        },
        trusted_param_keys={CHAT_INTERFACE_LAYER_FIELD, CHAT_INTERFACE_L2_MODE_FIELD},
    )
    assert fields2 == []


def test_reference_fill_cisco_routed_includes_no_switchport_and_ip():
    intent = {
        "target_hosts": ["Acc_Cisco_1"],
        "params": {
            "interfaces": [
                {
                    "name": "Ethernet0/3",
                    "mode": "routed",
                    "ip_address": "192.168.10.1",
                    "subnet_mask": "255.255.255.0",
                    "enabled": True,
                }
            ]
        },
    }
    rendered = fill_playbook_from_reference("interface", ["cisco_ios"], intent)
    assert isinstance(rendered, str) and rendered
    assert "no switchport" in rendered
    # IP is applied at Ansible runtime (tasks are inside {% raw %}); template must contain ip stanza.
    assert "ip address" in rendered and ("item.ip.address" in rendered or "item.ip_address" in rendered)


def test_prompt_aware_disambiguation_skips_trunk_vlan_question_when_prompt_has_vlans():
    schema = {"action": "configure_interface"}
    params_dis = {
        "question": "Please provide the following.",
        "fields": [{"field": "trunk_vlans", "question": "Which VLAN IDs should be allowed on this trunk?"}],
    }
    out = merge_prompt_aware_disambiguation_for_schema(
        "Configure Gi1/0/5 as trunk and allow VLANs 10, 20, 67 on SW1",
        schema,
        params_dis,
        parameters={"interfaces": [{"name": "Gi1/0/5", "mode": "trunk"}]},
    )
    fields = list((out or {}).get("fields") or [])
    assert all(str(f.get("field") or "") != "trunk_vlans" for f in fields if isinstance(f, dict))
