"""Schema inspector vs nettopo shutdown-all (interfaces_to_shutdown omitted)."""

from ansible.intent_validation import (
    build_intent_schema_inspector_payload,
    get_schema_for_parsed_intent,
    validate_completeness,
)


def test_validate_completeness_skips_interfaces_when_shutdown_use_nettopo():
    intent = {
        "action": "shutdown_interface",
        "feature": "interface",
        "parameters": {
            "target_hosts": ["Acc_Huawei_3"],
            "interfaces_shutdown_use_nettopo": True,
            "interfaces_admin_enabled": True,
        },
    }
    schema = get_schema_for_parsed_intent(intent)
    assert schema
    missing, _errs = validate_completeness(intent, schema, action_or_feature="shutdown_interface")
    assert "interfaces" not in missing


def test_inspector_expands_nettopo_shutdown_using_planned_preview():
    parsed = {
        "action": "shutdown_interface",
        "feature": "interface",
        "resolved_devices": ["Acc_Huawei_3"],
        "parameters": {
            "target_hosts": ["Acc_Huawei_3"],
            "interfaces_shutdown_use_nettopo": True,
            "interfaces_admin_enabled": True,
        },
        "planned_all_interfaces_preview": {
            "devices": [
                {
                    "hostname": "Acc_Huawei_3",
                    "interfaces": [
                        {"name": "GigabitEthernet1/0/1"},
                        {"name": "GigabitEthernet1/0/2"},
                    ],
                }
            ]
        },
    }
    out = build_intent_schema_inspector_payload(parsed, resolved_hosts=["Acc_Huawei_3"])
    primary = out["slices"][0]
    assert "interfaces" not in (primary.get("missing_required") or [])
    iface_row = next(
        (f for f in primary.get("fields") or [] if f.get("schema_field") == "interfaces"),
        None,
    )
    assert iface_row is not None
    assert "GigabitEthernet1/0/1" in (iface_row.get("value_preview") or "")
    assert "bring" in (primary.get("note") or "").lower()
    assert "bring up" in (iface_row.get("label") or "").lower() or "bring" in (
        iface_row.get("question") or ""
    ).lower()
