import json

from llm.controller_flow import build_controller_flow_state


def test_compare_tool_does_not_satisfy_planned_domain_from_live_only_payload():
    flow_state = build_controller_flow_state(
        user_message="What is the management IP of SW9 in inventory?",
        query_class="inventory_only",
        declared_required_domains=["planned_inventory", "planned_topology"],
        hostnames=["SW9"],
        tool_results={
            "compare_planned_vs_live": json.dumps(
                {
                    "devices": [
                        {
                            "hostname": "SW9",
                            "presence_state": "live_only",
                            "management_ip_alignment": "live_only",
                        }
                    ],
                    "live_only": ["SW9"],
                    "planned_only": [],
                    "live_source_metadata": {"source": "crawler_cache", "scope": "live_topology", "freshness_class": "live"},
                }
            )
        },
        tool_args={"compare_planned_vs_live": {"hostname": "SW9"}},
        allowed_tools=["compare_planned_vs_live", "get_inventory_device"],
    )

    assert flow_state["observe"]["satisfied_domains"] == []
    assert flow_state["release"]["missing_domains"] == ["planned_inventory"]
    assert flow_state["release"]["decision"] in {"reroute", "block"}


def test_digital_twin_self_correction_can_satisfy_telemetry_observation_domain():
    flow_state = build_controller_flow_state(
        user_message="Is SW1 down right now?",
        query_class="live_only",
        declared_required_domains=["live_topology", "telemetry"],
        hostnames=["SW1"],
        tool_results={
            "get_device_details": json.dumps(
                {
                    "hostname": "SW1",
                    "status": "up",
                }
            ),
            "get_digital_twin": json.dumps(
                {
                    "summary": {"live_link_count": 1, "telemetry_device_count": 0},
                    "freshness": {
                        "telemetry": {
                            "source": "influxdb",
                            "scope": "device_metrics",
                            "freshness_class": "missing",
                        }
                    },
                    "twins": [
                        {
                            "hostname": "SW1",
                            "presence_state": "live_only",
                            "live_status": "up",
                            "telemetry": None,
                        }
                    ],
                }
            ),
        },
        tool_args={
            "get_device_details": {"hostname": "SW1"},
            "get_digital_twin": {"hostname": "SW1"},
        },
        allowed_tools=["get_device_details", "get_device_telemetry_snapshot", "get_digital_twin"],
        self_correction_state={
            "attempts": [
                {
                    "tool_name": "get_device_telemetry_snapshot",
                    "failure_kind": "missing_telemetry",
                    "outcome": "retry_succeeded",
                    "revised_actions": [
                        {"name": "get_digital_twin", "arguments": {"hostname": "SW1"}},
                    ],
                }
            ]
        },
    )

    assert "telemetry" in flow_state["observe"]["satisfied_domains"]
    assert flow_state["observe"]["domain_coverage"]["telemetry"] == ["get_digital_twin"]
    assert flow_state["release"]["decision"] == "allow"