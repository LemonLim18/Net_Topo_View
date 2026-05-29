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
    _inventory_hostnames_mentioned_in_prompt,
    _is_management_interface,
    _normalize_mismatched_qualified_interface_tokens,
    _normalize_secondary_intents_qualified_interface_tokens,
    _resolve_target_hosts,
    _resolve_unknown_device_aliases,
)
from ansible import api as ansible_api
from ansible.context_entity_validation import build_planned_all_interfaces_preview
from ansible.intent_validation import (
    CHAT_INTERFACE_LAYER_FIELD,
    CHAT_INTERFACE_L2_MODE_FIELD,
    apply_chat_interface_wizard_to_parameters,
    configure_interface_conditional_disambiguation_fields,
    configure_ospf_conditional_disambiguation_fields,
    correct_ospf_params_from_natural_language,
    configure_interface_phased_wizard_fields,
    configure_interface_prompt_is_layer_ambiguous,
    correct_interface_l3_params_from_natural_language,
)
from ansible.api import _coerce_params_for_validation


class _ExactAliasDevice:
    def __init__(self, hostname: str, role: str = "access", vendor: str = "juniper_junos"):
        self.hostname = hostname
        self.role = role
        self.vendor = vendor


class _ExactAliasDb:
    def get_all_devices(self):
        return [
            _ExactAliasDevice("Juniper_Acc_4"),
            _ExactAliasDevice("Cisco_Acc_1", vendor="cisco_ios"),
        ]


def test_inventory_hostname_space_alias_resolves_to_planned_hostname(monkeypatch):
    db = _ExactAliasDb()
    monkeypatch.setattr(ansible_api, "_db", db)

    assert _inventory_hostnames_mentioned_in_prompt("Turn the interfaces of Juniper Acc 4 off", db) == [
        "Juniper_Acc_4"
    ]
    assert _resolve_target_hosts(["Juniper Acc 4"]) == ["Juniper_Acc_4"]
    assert _extract_non_exact_device_aliases_from_prompt("Turn the interfaces of Juniper Acc 4 off", db) == []


def test_coerce_params_ospf_atomic_fields_override_stale_networks():
    p = {
        "networks": [{"network": "192.168.1.0", "wildcard": "0.0.0.255", "area": 0}],
        "network_address": "192.168.9.0",
        "prefix_length": 29,
        "area": 9,
    }
    _coerce_params_for_validation(p)
    assert len(p["networks"]) == 1
    assert p["networks"][0]["network"] == "192.168.9.0"
    assert p["networks"][0]["area"] == 9
    wc = str(p["networks"][0].get("wildcard") or "")
    assert wc and wc.count(".") == 3
from ansible.intent_tree_engine import evaluate_intent_tree
from ansible.intent_validation import (
    correct_ospf_params_from_natural_language,
    get_schema_for_parsed_intent,
    strip_llm_ospf_advertisement_guesses_if_not_explicit_in_prompt,
    strip_llm_ospf_passive_guesses_if_not_explicit_in_prompt,
    strip_llm_ospf_process_id_if_not_explicit_in_prompt,
    validate_completeness,
)
from ansible.intent_validation import (
    extract_vlan_ids_for_trunk_allowed_from_prompt,
    merge_prompt_aware_disambiguation_for_schema,
)
from ansible.reference_resolver import fill_playbook_from_reference
from ansible.reference_resolver import get_template_vars


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
    expected = {"action": "create_vlan", "feature": "vlan", "parameters": {"_canonical_operation": "create_vlan"}}
    assert prepare_llm_input_dict(d) == expected


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


def test_configure_ospf_conditional_asks_all_missing_fields_in_one_pass():
    fields0 = configure_ospf_conditional_disambiguation_fields({"process_id": 1})
    assert [str(f.get("field") or "") for f in fields0] == ["network_address", "prefix_length", "area"]

    fields1 = configure_ospf_conditional_disambiguation_fields(
        {"process_id": 1, "network_address": "192.168.1.0"}
    )
    assert [str(f.get("field") or "") for f in fields1] == ["prefix_length", "area"]

    fields2 = configure_ospf_conditional_disambiguation_fields(
        {"process_id": 1, "network_address": "192.168.1.0", "prefix_length": 24}
    )
    assert [str(f.get("field") or "") for f in fields2] == ["area"]


def test_coerce_builds_ospf_networks_from_atomic_fields():
    p = {"network_address": "192.168.1.0", "prefix_length": "/24", "area": "0"}
    _coerce_params_for_validation(p)
    assert p.get("networks") == [{
        "network": "192.168.1.0",
        "wildcard": "0.0.0.255",
        "area": 0,
    }]


def test_correct_ospf_params_from_natural_language_fills_router_id_and_network():
    pi = {
        "action": "configure_ospf",
        "feature": "ospf",
        "parameters": {"process_id": 1, "target_hosts": ["Acc_Cisco_2"]},
    }
    correct_ospf_params_from_natural_language(
        "Set router ID 1.1.1.1 and advertise 10.0.0.0/24 in area 0 on SW1",
        pi,
    )
    p = pi["parameters"]
    assert p.get("router_id") == "1.1.1.1"
    assert p.get("network_address") == "10.0.0.0"
    assert p.get("prefix_length") == 24
    assert p.get("area") == 0
    assert p.get("networks") == [{"network": "10.0.0.0", "wildcard": "0.0.0.255", "area": 0}]


def test_correct_ospf_params_from_natural_language_extracts_passive_interface():
    pi = {
        "action": "configure_ospf",
        "feature": "ospf",
        "parameters": {"process_id": 1, "target_hosts": ["Acc_Cisco_1"]},
    }
    correct_ospf_params_from_natural_language(
        "Make GigabitEthernet1/0/1 passive in OSPF on router 1",
        pi,
    )
    assert pi["parameters"].get("passive_interfaces") == ["GigabitEthernet1/0/1"]


def test_get_schema_for_parsed_intent_uses_passive_ospf_schema():
    intent = {
        "action": "configure_ospf",
        "feature": "ospf",
        "parameters": {
            "target_hosts": ["Acc_Cisco_1"],
            "process_id": 1,
            "passive_interfaces": ["GigabitEthernet1/0/1"],
        },
    }
    schema = get_schema_for_parsed_intent(intent)
    assert schema is not None
    assert str(schema.get("action") or "") == "configure_ospf_passive_interface"


def test_get_schema_for_parsed_intent_uses_ospf_redistribute_schema():
    intent = {
        "action": "configure_ospf",
        "feature": "ospf",
        "parameters": {
            "target_hosts": ["Acc_Cisco_1"],
            "process_id": 1,
            "redistribution": ["static"],
        },
    }
    schema = get_schema_for_parsed_intent(intent)
    assert schema is not None
    assert str(schema.get("action") or "") == "configure_ospf_redistribute"


def test_get_schema_for_parsed_intent_redistribute_string_selects_redistribute_schema():
    intent = {
        "action": "configure_ospf",
        "feature": "ospf",
        "parameters": {
            "target_hosts": ["Acc_Cisco_1"],
            "redistribute": "static",
        },
    }
    schema = get_schema_for_parsed_intent(intent)
    assert schema is not None
    assert str(schema.get("action") or "") == "configure_ospf_redistribute"


def test_strip_llm_ospf_advertisement_surfaces_missing_networks_for_vague_prompt():
    parsed = {
        "action": "configure_ospf",
        "feature": "ospf",
        "resolved_devices": ["Acc_Cisco_1"],
        "parameters": {
            "process_id": 1,
            "networks": [{"network": "192.168.1.0", "wildcard": "0.0.0.255", "area": 0}],
            "router_id": "192.168.1.40",
        },
        "device_deduction": {},
    }
    strip_llm_ospf_advertisement_guesses_if_not_explicit_in_prompt(
        "Configure OSPF on devices",
        parsed,
        client_supplied_advertisement=False,
        client_supplied_router_id=False,
    )
    assert not parsed["parameters"].get("networks")
    assert not parsed["parameters"].get("router_id")
    sch = get_schema_for_parsed_intent(parsed)
    assert sch is not None
    missing, _ = validate_completeness(parsed, sch, action_or_feature="configure_ospf")
    assert "networks" in missing


def test_strip_llm_ospf_advertisement_keeps_networks_when_cidr_in_prompt():
    parsed = {
        "action": "configure_ospf",
        "feature": "ospf",
        "parameters": {},
        "device_deduction": {},
    }
    correct_ospf_params_from_natural_language(
        "Advertise 10.0.0.0/24 in OSPF area 0 on router 1",
        parsed,
    )
    strip_llm_ospf_advertisement_guesses_if_not_explicit_in_prompt(
        "Advertise 10.0.0.0/24 in OSPF area 0 on router 1",
        parsed,
        client_supplied_advertisement=False,
        client_supplied_router_id=False,
    )
    assert parsed["parameters"].get("networks")


def test_strip_llm_ospf_process_id_surfaces_wizard_when_prompt_implicit():
    parsed = {
        "action": "configure_ospf",
        "feature": "ospf",
        "parameters": {"process_id": 1, "redistribution": ["static"]},
        "device_deduction": {},
    }
    strip_llm_ospf_process_id_if_not_explicit_in_prompt(
        "Redistribute static routes into OSPF on router 2",
        parsed,
        client_supplied_process_id=False,
    )
    assert "process_id" not in parsed["parameters"]
    res = evaluate_intent_tree(
        intent_description="Redistribute static routes into OSPF on router 2",
        parsed_intent=parsed,
        resolved_hosts=["R2"],
        db_manager=None,
    )
    fields = (res.params_disambiguation or {}).get("fields") or []
    names = {f.get("field") for f in fields if isinstance(f, dict)}
    assert "process_id" in names


def test_strip_llm_ospf_passive_guess_for_network_prompt():
    parsed = {
        "action": "configure_ospf_passive_interface",
        "feature": "ospf",
        "parameters": {
            "process_id": 1,
            "network_address": "192.168.1.0",
            "prefix_length": 24,
            "area": 0,
            "passive_interfaces": ["GigabitEthernet1/0/1"],
            "_canonical_operation": "configure_ospf_passive_interface",
        },
    }
    strip_llm_ospf_passive_guesses_if_not_explicit_in_prompt(
        "Add network 192.168.1.0/24 to OSPF area 0 on router 2",
        parsed,
        client_supplied_passive_interfaces=False,
    )
    p = parsed["parameters"]
    assert "passive_interfaces" not in p
    assert str(parsed.get("action") or "") == "configure_ospf"
    assert str(p.get("_canonical_operation") or "") == "configure_ospf"


def test_keep_ospf_passive_when_prompt_explicit():
    parsed = {
        "action": "configure_ospf_passive_interface",
        "feature": "ospf",
        "parameters": {
            "process_id": 1,
            "passive_interfaces": ["GigabitEthernet1/0/1"],
            "_canonical_operation": "configure_ospf_passive_interface",
        },
    }
    strip_llm_ospf_passive_guesses_if_not_explicit_in_prompt(
        "Make GigabitEthernet1/0/1 passive in OSPF on router 1",
        parsed,
        client_supplied_passive_interfaces=False,
    )
    p = parsed["parameters"]
    assert p.get("passive_interfaces") == ["GigabitEthernet1/0/1"]
    assert str(parsed.get("action") or "") == "configure_ospf_passive_interface"


def test_configure_interface_conditional_does_not_require_native_vlan_for_trunk_ports():
    fields = configure_interface_conditional_disambiguation_fields(
        {
            "interfaces": [
                {"name": "Acc_Cisco_1:Ethernet0/2", "mode": "trunk", "trunk_vlans": [10, 20]},
            ]
        }
    )
    asked = {str(f.get("field") or "") for f in fields}
    assert "native_vlan" not in asked


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
    assert 'parents: "interface {{ (item.name | string).split(' in rendered


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


def test_reference_fill_huawei_acl_reports_change_correctly_in_check_mode():
    intent = {
        "target_hosts": ["Acc_Huawei_3"],
        "params": {
            "acl_name": "BLOCK_ICMP",
            "acl_type": "ipv4",
            "rules": [
                {
                    "action": "deny",
                    "protocol": "icmp",
                    "source": "any",
                    "destination": "any",
                }
            ],
            "apply_to": ["GigabitEthernet1/0/3 in"],
        },
    }
    rendered = fill_playbook_from_reference("acl", ["huawei_vrp"], intent)
    assert isinstance(rendered, str) and rendered
    assert "display current-configuration | include acl name BLOCK_ICMP" in rendered
    assert "display current-configuration interface GE1/0/3 | include traffic-filter" in rendered
    assert "Apply ACL to GE1/0/3 on Huawei VRP" in rendered
    assert "rule 10 deny icmp source any destination any" in rendered
    assert '_acl_identifier: "acl name BLOCK_ICMP"' in rendered
    assert 'changed_when: "{{ (_acl_would_change | bool) if ansible_check_mode else (acl_cfg_result.changed | default(false)) }}"' in rendered
    assert "traffic-filter inbound acl name BLOCK_ICMP" in rendered
    assert "undo traffic-filter inbound" not in rendered
    assert "undo traffic-filter outbound" not in rendered
    assert 'changed_when: "{{ (_tf_would_change | bool) if ansible_check_mode else (tf_apply_result.changed | default(false)) }}"' in rendered
    assert "{{ acl_name }}" not in rendered
    assert "{{ _dir" not in rendered


def test_reference_fill_multi_vendor_acl_has_huawei_prechecks_and_changed_logic():
    intent = {
        "target_hosts": ["Acc_Cisco_1", "Acc_Huawei_3"],
        "params": {
            "acl_name": "2000",
            "acl_type": "ipv4",
            "rules": [
                {
                    "action": "permit",
                    "protocol": "tcp",
                    "source": "192.168.1.0",
                    "source_wildcard": "0.0.0.255",
                    "destination": "any",
                    "port": 22,
                }
            ],
            "apply_to": ["GigabitEthernet1/0/3 in"],
        },
    }
    rendered = fill_playbook_from_reference("acl", ["cisco_ios", "huawei_vrp"], intent)
    assert isinstance(rendered, str) and rendered
    assert "register: acl_precheck_huawei" in rendered
    assert "display current-configuration | include acl number 2000" in rendered
    assert "register: acl_cfg_result_huawei" in rendered
    assert "register: tf_precheck_huawei" in rendered
    assert "register: tf_apply_result_huawei" in rendered
    assert "display current-configuration interface GE1/0/3 | include traffic-filter" in rendered
    assert "Apply ACL to GE1/0/3 on Huawei VRP" in rendered
    assert "interface GigabitEthernet1/0/3" in rendered  # Cisco path remains unchanged.
    assert "rule 10 permit tcp source 192.168.1.0 0.0.0.255 destination any destination-port eq 22" in rendered
    assert "undo traffic-filter inbound" not in rendered
    assert "undo traffic-filter outbound" not in rendered
    assert 'changed_when: "{{ (_tf_would_change | bool) if ansible_check_mode else (tf_apply_result_huawei.changed | default(false)) }}"' in rendered
    assert "traffic-filter inbound acl 2000" in rendered


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


def test_reference_fill_ospf_cisco_renders_network_lines():
    intent = {
        "target_hosts": ["Acc_Cisco_2"],
        "params": {
            "process_id": 1,
            "router_id": "1.1.1.1",
            "networks": [{"network": "192.168.1.0", "wildcard": "0.0.0.255", "area": 0}],
        },
    }
    rendered = fill_playbook_from_reference("ospf", ["cisco_ios"], intent)
    assert isinstance(rendered, str) and rendered
    assert 'parents: "router ospf 1"' in rendered
    assert "router-id 1.1.1.1" in rendered
    assert "network 192.168.1.0 0.0.0.255 area 0" in rendered


def test_reference_fill_ospf_cisco_renders_passive_interface_lines():
    intent = {
        "target_hosts": ["Acc_Cisco_1"],
        "params": {
            "process_id": 1,
            "router_id": "1.1.1.1",
            "passive_interfaces": ["Acc_Cisco_1:Ethernet0/1"],
        },
    }
    rendered = fill_playbook_from_reference("ospf", ["cisco_ios"], intent)
    assert isinstance(rendered, str) and rendered
    assert 'parents: "router ospf 1"' in rendered
    assert "router-id 1.1.1.1" in rendered
    assert "passive-interface Ethernet0/1" in rendered


def test_reference_fill_ospf_cisco_renders_redistribution_lines():
    intent = {
        "target_hosts": ["Acc_Cisco_1"],
        "params": {
            "process_id": 1,
            "redistribution": ["static", "connected"],
        },
    }
    rendered = fill_playbook_from_reference("ospf", ["cisco_ios"], intent)
    assert isinstance(rendered, str) and rendered
    assert "redistribute static" in rendered
    assert "redistribute connected" in rendered


def test_reference_fill_ospf_huawei_maps_passive_interface_to_short_name():
    intent = {
        "target_hosts": ["Acc_Huawei_3"],
        "params": {
            "process_id": 1,
            "passive_interfaces": ["Acc_Huawei_3:GigabitEthernet1/0/3"],
        },
    }
    rendered = fill_playbook_from_reference("ospf", ["huawei_vrp"], intent)
    assert isinstance(rendered, str) and rendered
    assert "silent-interface GE1/0/3" in rendered
    assert "silent-interface GigabitEthernet1/0/3" not in rendered


def test_reference_fill_static_route_multi_vendor_maps_huawei_next_hop_only():
    intent = {
        "target_hosts": ["Acc_Cisco_1", "Acc_Huawei_3"],
        "params": {
            "destination": "10.10.10.0/24",
            "next_hop": "GigabitEthernet1/0/3",
        },
    }
    rendered = fill_playbook_from_reference("static_route", ["cisco_ios", "huawei_vrp"], intent)
    assert isinstance(rendered, str) and rendered
    assert "ip route 10.10.10.0 255.255.255.0 GigabitEthernet1/0/3" in rendered
    assert "ip route-static 10.10.10.0 255.255.255.0 GE1/0/3" in rendered


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


def test_reference_fill_juniper_interface_uses_json_var_injection_for_shutdown_lists():
    intent = {
        "target_hosts": ["Juniper_Acc_4"],
        "params": {
            "interfaces_to_shutdown": ["ge-0/0/1"],
            "target_hosts": ["Juniper_Acc_4"],
            "target_devices": ["Juniper_Acc_4"],
        },
    }
    rendered = fill_playbook_from_reference("interface", ["juniper_junos"], intent)
    assert isinstance(rendered, str) and rendered
    assert "interfaces_config: []" in rendered
    assert 'interfaces_to_shutdown: ["ge-0/0/1"]' in rendered or "interfaces_to_shutdown:\n  - ge-0/0/1" in rendered


def test_juniper_template_vars_keep_explicit_shutdown_list_when_nettopo_flag_is_set():
    intent = {
        "target_hosts": ["Juniper_Acc_4"],
        "params": {
            "interfaces_to_shutdown": ["ge-0/0/1"],
            "interfaces_shutdown_use_nettopo": True,
            "target_hosts": ["Juniper_Acc_4"],
            "target_devices": ["Juniper_Acc_4"],
        },
    }
    vars_out = get_template_vars("interface", intent, "")
    assert vars_out.get("interfaces_shutdown_use_nettopo") is False
    assert vars_out.get("interfaces_to_shutdown_json") == '["ge-0/0/1"]'


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


def test_extract_vlan_ids_for_trunk_allowed_from_prompt_variants():
    assert extract_vlan_ids_for_trunk_allowed_from_prompt(
        "Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on SW1"
    ) == [10, 20, 67]
    assert extract_vlan_ids_for_trunk_allowed_from_prompt(
        "Configure Gi1/0/5 as trunk and allow VLANs. 10, 20, 67 on SW1"
    ) == [10, 20, 67]
    assert extract_vlan_ids_for_trunk_allowed_from_prompt(
        "Set port as trunk and allow 10, 20, and 67"
    ) == [10, 20, 67]


def test_prompt_aware_disambiguation_strips_trunk_vlan_after_reappend_simulating_secondary():
    """Mirrors preview flow: merge strips trunk_vlans, then _append_secondary re-adds it."""
    schema = {"action": "configure_interface"}
    prompt = "Configure Gi1/0/5 as trunk and allow VLANs 10, 20, 67 on SW1"
    params = {"interfaces": [{"name": "Gi1/0/5", "mode": "trunk"}]}
    out1 = merge_prompt_aware_disambiguation_for_schema(
        prompt,
        schema,
        {
            "question": "Please provide the following.",
            "fields": [{"field": "trunk_vlans", "question": "Which VLAN IDs should be allowed on this trunk?"}],
        },
        parameters=params,
    )
    fields2 = list((out1 or {}).get("fields") or [])
    fields2.append(
        {
            "field": "trunk_vlans",
            "question": "Which VLAN IDs should be allowed on this trunk?",
            "secondary_intent_index": 0,
        }
    )
    out2 = merge_prompt_aware_disambiguation_for_schema(
        prompt,
        schema,
        {"question": "Please provide the following.", "fields": fields2},
        parameters=params,
    )
    assert all(str(f.get("field") or "") != "trunk_vlans" for f in (out2 or {}).get("fields") or [] if isinstance(f, dict))
