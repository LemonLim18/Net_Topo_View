from llm.answer_grounding import build_answer_grounding, format_conflict_summary


def test_build_answer_grounding_detects_compare_conflicts():
    grounding = build_answer_grounding(
        query_class="planned_vs_live",
        tool_results={
            "compare_planned_vs_live": {
                "devices": [
                    {
                        "hostname": "SW1",
                        "presence_state": "planned_and_live",
                        "management_ip_alignment": "mismatch",
                        "planned_management_ip": "10.0.0.2",
                        "live_management_ip": "10.0.0.22",
                    }
                ],
                "source_used": "local_db",
                "inventory_source_metadata": {"source": "local_db", "scope": "planned_inventory", "confidence": 0.85},
                "live_source_metadata": {"source": "crawler_cache", "scope": "live_topology", "confidence": 0.9},
            }
        },
        tool_args={"compare_planned_vs_live": {"hostname": "SW1"}},
    )

    assert grounding["answer_scope"] == "mixed"
    assert any(conflict["code"] == "management_ip_mismatch" for conflict in grounding["conflicts"])
    assert "Conflicts:" in format_conflict_summary(grounding)


def test_build_answer_grounding_detects_operational_conflicts_across_sources():
    grounding = build_answer_grounding(
        query_class="troubleshooting",
        tool_results={
            "get_inventory_device": {
                "device": {"hostname": "SW1", "management_ip": "10.0.0.2", "status": "active"},
                "source_used": "local_db",
                "source_metadata": {"source": "local_db", "scope": "planned_inventory", "confidence": 0.85},
            },
            "get_device_details": {
                "hostname": "SW1",
                "status": "down",
                "mgmt_ip": "10.0.0.2",
                "source_metadata": {"source": "crawler_cache", "scope": "live_topology", "confidence": 0.9},
            },
            "get_device_running_config": {
                "hostname": "SW1",
                "note": "Run a discovery crawl to capture the running configuration.",
                "source_metadata": {"source": "config_snapshots", "scope": "device_configs", "freshness_class": "missing", "confidence": 0.0},
            },
            "get_recent_drift_events": {
                "events": [{"hostname": "SW1", "has_drift": True, "severity": "high", "timestamp": "2026-03-09T10:12:00+00:00"}],
                "source_metadata": {"source": "drift_store", "scope": "drift_state", "confidence": 0.9},
            },
            "get_recent_incidents": {
                "incidents": [{"id": "INC-1", "severity": "high", "status": "open", "affected_devices": ["SW1"]}],
                "source_metadata": {"source": "incident_store", "scope": "incident_state", "confidence": 0.9},
            },
        },
        tool_args={
            "get_inventory_device": {"hostname": "SW1"},
            "get_device_details": {"hostname": "SW1"},
            "get_device_running_config": {"hostname": "SW1"},
            "get_recent_drift_events": {"hostname": "SW1"},
        },
    )

    codes = {conflict["code"] for conflict in grounding["conflicts"]}
    assert "operational_state_conflict" in codes
    assert "config_snapshot_missing" in codes
    assert "config_drift_detected" in codes
    assert "active_incident" in codes