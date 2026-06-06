import json

from llm.controller_self_correction import build_self_correction_state, plan_self_correction


def test_self_correction_repairs_malformed_inventory_arguments():
    state = build_self_correction_state()

    plan = plan_self_correction(
        user_message="What is the management IP of SW1 in inventory?",
        tool_name="get_inventory_device",
        arguments={"device": "SW1"},
        raw_result=json.dumps({"error": "get_inventory_device() got an unexpected keyword argument 'device'"}),
        route_plan={"hostnames": ["SW1"], "allowed_tools": ["get_inventory_device", "search_inventory_devices"]},
        tool_results={},
        state=state,
    )

    assert plan is not None
    assert plan["decision"] == "retry"
    assert plan["entry"]["failure_kind"] == "malformed_arguments"
    assert plan["revised_tool_calls"][0].name == "get_inventory_device"
    assert plan["revised_tool_calls"][0].arguments == {"hostname": "SW1"}


def test_self_correction_escalates_after_retry_cap():
    state = build_self_correction_state(max_attempts=2)
    state["attempts"] = [
        {"tool_name": "get_inventory_device", "failure_kind": "malformed_arguments", "attempt": 1, "outcome": "retry_failed"},
        {"tool_name": "get_inventory_device", "failure_kind": "malformed_arguments", "attempt": 2, "outcome": "retry_failed"},
    ]

    plan = plan_self_correction(
        user_message="What is the management IP of SW1 in inventory?",
        tool_name="get_inventory_device",
        arguments={"device": "SW1"},
        raw_result=json.dumps({"error": "get_inventory_device() got an unexpected keyword argument 'device'"}),
        route_plan={"hostnames": ["SW1"], "allowed_tools": ["get_inventory_device"]},
        tool_results={},
        state=state,
    )

    assert plan is not None
    assert plan["decision"] == "escalate"
    assert "Retry cap reached" in plan["escalation_reason"]
    assert plan["entry"]["outcome"] == "escalated"


def test_self_correction_requests_template_context_for_schema_violation():
    state = build_self_correction_state()
    tool_results = {
        "parse_intent": json.dumps({"feature": "vlan", "vendor": "cisco_ios"}),
    }

    plan = plan_self_correction(
        user_message="Configure VLAN 20 on SW1",
        tool_name="plan_change",
        arguments={"intent_json": "{}"},
        raw_result=json.dumps({"error": "schema validation failed: missing required property 'vlans'"}),
        route_plan={"hostnames": ["SW1"], "allowed_tools": ["plan_change", "get_template_schema", "list_templates"]},
        tool_results=tool_results,
        state=state,
    )

    assert plan is not None
    assert plan["decision"] == "retry"
    names = [call.name for call in plan["revised_tool_calls"]]
    assert names == ["get_template_schema", "list_templates"]


def test_self_correction_retry_counter_resets_after_success():
    state = build_self_correction_state(max_attempts=2)
    state["attempts"] = [
        {"tool_name": "get_device_telemetry_snapshot", "failure_kind": "missing_telemetry", "attempt": 1, "outcome": "retry_failed"},
        {"tool_name": "get_device_telemetry_snapshot", "failure_kind": "missing_telemetry", "attempt": 2, "outcome": "retry_succeeded"},
    ]

    plan = plan_self_correction(
        user_message="Is SW1 down right now?",
        tool_name="get_device_telemetry_snapshot",
        arguments={"hostname": "SW1", "limit": 10},
        raw_result=json.dumps({"error": "telemetry backend timeout"}),
        route_plan={"hostnames": ["SW1"], "allowed_tools": ["get_device_telemetry_snapshot", "get_digital_twin", "get_recent_incidents"]},
        tool_results={},
        state=state,
    )

    assert plan is not None
    assert plan["decision"] == "retry"
    assert plan["entry"]["attempt"] == 1