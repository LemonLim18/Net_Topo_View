"""Unit tests for refactored intent pipeline (no LLM)."""

from ansible.intent_pipeline.cross_check import cross_check_parsed_intent
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
