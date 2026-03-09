import json

from db.manager import DatabaseManager
from db.models import ConfigSnapshot, Device, DriftEvent
from incidents.store import IncidentStore
from llm.change_intelligence import CHANGE_INTELLIGENCE_VERSION, analyze_change_intelligence
from llm.intent import IntentScope, IntentType, ParsedIntent, RiskLevel


def _build_db(tmp_path):
    db_path = tmp_path / "step29.db"
    db = DatabaseManager(str(db_path))
    db.initialize()
    return db, str(db_path)


def _seed_sw1(db):
    device_id = db.upsert_device(
        Device(
            hostname="SW1",
            management_ip="10.0.0.2",
            vendor="cisco_ios",
            status="active",
            role="access",
            source="netbox",
            last_polled="2026-03-09T10:00:00+00:00",
        )
    )
    db.save_config_snapshot(
        ConfigSnapshot(
            device_id=device_id,
            config_text="hostname SW1\ninterface Vlan10\n ip address 10.0.0.2 255.255.255.0\n",
            captured_at="2026-03-09T10:05:00+00:00",
        )
    )
    return device_id


def test_change_intelligence_surfaces_cross_domain_risk_and_rejects_unstable_change(tmp_path):
    db, db_path = _build_db(tmp_path)
    device_id = _seed_sw1(db)
    db.update_topology(
        {
            "timestamp": "2026-03-09T10:10:00+00:00",
            "nodes": {
                "SW1": {
                    "mgmt_ip": "10.0.0.22",
                    "down": True,
                    "connectedNode": ["R1"],
                    "interfaceList": [{}],
                    "edgeLink": {"R1": {"state": "down"}},
                }
            },
        }
    )
    db.save_drift_event(
        DriftEvent(
            device_id=device_id,
            hostname="SW1",
            has_drift=True,
            severity="high",
            diff_text="interface Vlan20 missing",
            timestamp="2026-03-09T10:11:00+00:00",
        )
    )
    db.insert_security_event(
        event_type="brute_force",
        severity="high",
        description="Repeated SSH failures against SW1",
        source_ip="10.0.0.2",
        device_id=device_id,
    )
    IncidentStore(db_path).create(
        anomaly_type="link_failure",
        severity="high",
        affected_devices=["SW1"],
        metrics={"current": 0.0, "baseline": 1.0, "threshold": 0.2},
        detected_at="2026-03-09T10:12:00+00:00",
    )

    intent = ParsedIntent(
        id="intent-step29",
        intent_type=IntentType.CONFIGURE,
        action="create_vlan",
        feature="vlan",
        vendor="cisco_ios",
        parameters={"vlans": [{"id": 20, "name": "users"}]},
        scope=IntentScope(device_hostnames=["SW1"]),
        risk_level=RiskLevel.MEDIUM,
        justification="Create user VLAN on SW1",
        raw_user_input="Create VLAN 20 on SW1",
        resolved_devices=["SW1"],
        created_at="2026-03-09T10:13:00+00:00",
    )

    report = analyze_change_intelligence(db, intent.model_dump_json(), user_role="operator")

    assert report["analysis_version"] == CHANGE_INTELLIGENCE_VERSION
    assert report["approval_recommendation"] == "reject"
    assert report["cross_domain_risk"]["level"] in {"high", "critical"}
    assert any(factor.startswith("active_incidents:") for factor in report["cross_domain_risk"]["factors"])
    assert any(conflict["code"] == "device_down" for conflict in report["evidence"]["planned_live_conflicts"])
    assert any(conflict["code"] == "management_ip_mismatch" for conflict in report["evidence"]["planned_live_conflicts"])
    assert report["root_cause_graph"]["hotspots"][0]["hostname"] == "SW1"
    assert any("down in live topology" in blocker for blocker in report["what_if_simulation"]["blockers"])


def test_change_intelligence_learns_from_similar_intents_and_failed_jobs(tmp_path):
    db, _db_path = _build_db(tmp_path)
    device_id = _seed_sw1(db)

    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO intents
                (id, user_prompt, intent_type, action, feature, vendor, parameters, scope,
                 risk_level, resolved_devices, policy_approved, policy_result, blast_radius, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-intent-1",
                "Create VLAN 20 on SW1",
                "configure",
                "create_vlan",
                "vlan",
                "cisco_ios",
                json.dumps({"vlans": [{"id": 20, "name": "users"}]}),
                json.dumps({"device_hostnames": ["SW1"], "all_devices": False}),
                "medium",
                json.dumps(["SW1"]),
                1,
                json.dumps({"approved": True, "warnings": []}),
                1,
                "executed",
                "2026-03-08T09:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO change_requests (title, description, status, requester) VALUES (?, ?, ?, ?)",
            ("VLAN rollout", "prior rollout", "failed", "operator"),
        )
        request_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO jobs (request_id, device_id, status, stage, error_message, generated_config, rollback_config)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                device_id,
                "failed",
                "apply",
                "Verification failed",
                "vlan 20",
                "no vlan 20",
            ),
        )
        job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO config_proposals
                (id, status, user_prompt, target_device, target_device_id, vendor, feature, params,
                 generated_config, rollback_config, diff_text, risk_level, ai_explanation, job_id, request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proposal-1",
                "rejected",
                "Create VLAN 20 on SW1",
                "SW1",
                device_id,
                "cisco_ios",
                "vlan",
                json.dumps({"vlans": [{"id": 20, "name": "users"}]}),
                "vlan 20",
                "no vlan 20",
                "",
                "medium",
                "prior attempt failed",
                job_id,
                request_id,
            ),
        )

    intent = ParsedIntent(
        id="intent-step29-2",
        intent_type=IntentType.CONFIGURE,
        action="create_vlan",
        feature="vlan",
        vendor="cisco_ios",
        parameters={"vlans": [{"id": 20, "name": "users"}]},
        scope=IntentScope(device_hostnames=["SW1"]),
        risk_level=RiskLevel.MEDIUM,
        justification="Retry VLAN creation after failed rollout",
        raw_user_input="Create VLAN 20 on SW1 again",
        resolved_devices=["SW1"],
        created_at="2026-03-09T10:13:00+00:00",
    )

    report = analyze_change_intelligence(db, intent.model_dump_json(), user_role="operator")

    assert report["intent_memory"]["total_similar"] >= 1
    assert report["intent_memory"]["device_overlap_count"] >= 1
    assert report["change_outcome_learning"]["job_outcomes"]["failed"] == 1
    assert report["change_outcome_learning"]["proposal_outcomes"]["rejected"] == 1
    assert any("failed or rolled back" in item for item in report["change_outcome_learning"]["recommended_guards"])
    assert report["what_if_simulation"]["decision"] in {"manual_review", "approve_with_caution", "reject"}