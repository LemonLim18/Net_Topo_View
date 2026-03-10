from llm.answer_grounding import build_answer_grounding, format_conflict_summary, format_operator_rationale


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


def test_build_answer_grounding_adds_step6_evidence_record_and_assumptions():
    grounding = build_answer_grounding(
        query_class="troubleshooting",
        tool_results={
            "get_device_details": {
                "hostname": "SW1",
                "status": "down",
                "source_metadata": {
                    "source": "crawler_cache",
                    "scope": "live_topology",
                    "freshness_class": "live",
                    "confidence": 0.9,
                },
            },
            "get_device_telemetry_snapshot": {
                "hostname": "SW1",
                "metrics": [],
                "total": 0,
                "source_metadata": {
                    "source": "influxdb",
                    "scope": "telemetry",
                    "freshness_class": "stale",
                    "confidence": 0.6,
                },
            },
        },
        tool_args={
            "get_device_details": {"hostname": "SW1"},
            "get_device_telemetry_snapshot": {"hostname": "SW1", "limit": 10},
        },
    )

    assert grounding["freshness_class"] == "stale"
    assert grounding["evidence_record"]["version"] == "phase2_step6_v1"
    assert grounding["evidence_record"]["answer_scope"] == grounding["answer_scope"]
    assert grounding["evidence_record"]["freshness_class"] == "stale"
    assert grounding["assumptions"]
    assert grounding["structured_rationale"]["current_state"]
    assert grounding["structured_rationale"]["verification_plan"]


def test_format_operator_rationale_includes_step6_sections():
    grounding = build_answer_grounding(
        query_class="inventory_only",
        tool_results={
            "get_inventory_device": {
                "device": {"hostname": "SW1", "management_ip": "10.0.0.2"},
                "source_used": "local_db",
                "source_metadata": {
                    "source": "local_db",
                    "scope": "planned_inventory",
                    "freshness_class": "live",
                    "confidence": 0.9,
                },
            }
        },
        tool_args={"get_inventory_device": {"hostname": "SW1"}},
    )

    rationale = format_operator_rationale(grounding)

    assert "Current state:" in rationale
    assert "Target state:" in rationale
    assert "Key risk or uncertainty:" in rationale
    assert "Verification plan:" in rationale


def test_build_answer_grounding_tracks_step7_evidence_authority_for_knowledge_queries():
    grounding = build_answer_grounding(
        query_class="knowledge_heavy",
        tool_results={
            "get_knowledge_base_status": {
                "documents": [{"id": "doc-1", "title": "OSPF Design"}],
                "total": 1,
                "source_used": "vector_store",
                "source_metadata": {
                    "source": "vector_store",
                    "scope": "knowledge_documents",
                    "freshness_class": "warm",
                    "confidence": 0.7,
                },
            }
        },
        tool_args={"get_knowledge_base_status": {"limit": 5}},
    )

    authority = grounding["evidence_authority"]

    assert authority["preferred_families"] == ["documentation"]
    assert authority["observed_families"] == ["documentation"]
    assert authority["preferred_evidence_present"] is True
    assert authority["substitution_risk"] is False


def test_build_answer_grounding_marks_supporting_live_family_for_inventory_query():
    grounding = build_answer_grounding(
        query_class="inventory_only",
        tool_results={
            "compare_planned_vs_live": {
                "devices": [
                    {
                        "hostname": "SW1",
                        "presence_state": "planned_and_live",
                        "management_ip_alignment": "match",
                    }
                ],
                "source_used": ["local_db", "crawler_cache"],
                "inventory_source_metadata": {"source": "local_db", "scope": "planned_inventory", "freshness_class": "live", "confidence": 0.88},
                "live_source_metadata": {"source": "crawler_cache", "scope": "live_topology", "freshness_class": "live", "confidence": 0.91},
            }
        },
        tool_args={"compare_planned_vs_live": {"hostname": "SW1"}},
    )

    authority = grounding["evidence_authority"]

    assert authority["preferred_families"] == ["planned"]
    assert authority["observed_families"] == ["planned", "live"]
    assert authority["supporting_families_used"] == ["live"]
    assert authority["requires_disclosure"] is True