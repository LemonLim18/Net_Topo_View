import json

from db.manager import DatabaseManager
from db.models import ConfigSnapshot, Device, DriftEvent, Link
from incidents.store import IncidentStore
from llm.context_contract import CONTEXT_DOMAIN_TELEMETRY, compute_freshness
from rag.context_bundle import ContextBundleBuilder, TelemetryContext
from rag.digital_twin import DIGITAL_TWIN_VERSION, DigitalTwinBuilder


def _build_db(tmp_path):
    db_path = tmp_path / "step30.db"
    db = DatabaseManager(str(db_path))
    db.initialize()
    return db, str(db_path)


def test_digital_twin_unifies_planned_live_config_telemetry_incidents_and_drift(tmp_path):
    db, db_path = _build_db(tmp_path)
    sw1_id = db.upsert_device(
        Device(
            hostname="SW1",
            management_ip="10.0.0.2",
            vendor="cisco_ios",
            status="active",
            role="access",
            source="netbox",
            last_polled="2026-03-10T10:00:00+00:00",
        )
    )
    r1_id = db.upsert_device(
        Device(
            hostname="R1",
            management_ip="10.0.0.1",
            vendor="cisco_ios",
            status="active",
            role="core",
            source="netbox",
            last_polled="2026-03-10T10:00:00+00:00",
        )
    )
    db.upsert_link(Link(src_device_id=sw1_id, dst_device_id=r1_id, discovery_protocol="manual"))
    db.save_config_snapshot(
        ConfigSnapshot(
            device_id=sw1_id,
            config_text="hostname SW1\ninterface Vlan10\n ip address 10.0.0.2 255.255.255.0\n",
            captured_at="2026-03-10T10:05:00+00:00",
        )
    )
    db.update_topology(
        {
            "timestamp": "2026-03-10T10:06:00+00:00",
            "nodes": {
                "SW1": {"mgmt_ip": "10.0.0.22", "down": False, "connectedNode": ["R1"], "interfaceList": [{}], "edgeLink": {}},
                "R1": {"mgmt_ip": "10.0.0.1", "down": False, "connectedNode": ["SW1"], "interfaceList": [{}], "edgeLink": {}},
            },
        }
    )
    db.save_drift_event(
        DriftEvent(
            device_id=sw1_id,
            hostname="SW1",
            has_drift=True,
            severity="high",
            diff_text="interface Vlan20 missing",
            timestamp="2026-03-10T10:07:00+00:00",
        )
    )
    IncidentStore(db_path).create(
        anomaly_type="interface_error",
        severity="high",
        affected_devices=["SW1"],
        metrics={"current": 90.0, "baseline": 2.0, "threshold": 10.0},
        detected_at="2026-03-10T10:08:00+00:00",
    )
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO config_proposals
                (id, status, user_prompt, target_device, target_device_id, vendor, feature, params,
                 generated_config, rollback_config, diff_text, risk_level, ai_explanation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proposal-step30",
                "pending",
                "Create VLAN 20 on SW1",
                "SW1",
                sw1_id,
                "cisco_ios",
                "vlan",
                json.dumps({"vlans": [{"id": 20, "name": "users"}]}),
                "hostname SW1\nvlan 20\n name users\n",
                "no vlan 20\n",
                "",
                "medium",
                "Create VLAN 20",
            ),
        )

    bundle_builder = ContextBundleBuilder(db_manager=db)
    bundle_builder._fetch_telemetry = lambda device_ips, hostnames=None, query=None, routing_decision=None: TelemetryContext(  # type: ignore[attr-defined]
        metrics=[
            {
                "device": "10.0.0.2",
                "metric": "cpu",
                "value": 72,
                "timestamp": "2026-03-10T10:09:00+00:00",
                "unit": "%",
            }
        ],
        anomaly_baselines=[{"device": "10.0.0.2", "metric": "cpu", "baseline": 20}],
        change_markers=[{"device": "10.0.0.2", "reason": "deployment"}],
        freshness=compute_freshness(
            domain=CONTEXT_DOMAIN_TELEMETRY,
            captured_at="2026-03-10T10:09:00+00:00",
            ingested_at="2026-03-10T10:09:00+00:00",
            source="influxdb",
            scope="device_metrics",
            confidence=0.9,
        ),
    )

    snapshot = DigitalTwinBuilder(db_manager=db, context_bundle_builder=bundle_builder).build_snapshot(["SW1"])
    payload = snapshot.to_dict()

    assert payload["version"] == DIGITAL_TWIN_VERSION
    assert payload["summary"]["twin_count"] == 1
    twin = payload["twins"][0]
    assert twin["hostname"] == "SW1"
    assert twin["presence_state"] == "planned_and_live"
    assert twin["intended_config"]["source"] == "config_proposals"
    assert twin["actual_config"]["source"] == "config_snapshots"
    assert twin["telemetry"]["trend_state"] == "changing"
    assert twin["incident_count"] == 1
    assert twin["drift_state"] == "drift_detected"
    conflict_codes = {conflict["code"] for conflict in payload["conflicts"]}
    assert "management_ip_mismatch" in conflict_codes
    assert "intended_actual_divergence" in conflict_codes
    assert "active_incident" in conflict_codes
    assert "config_drift_detected" in conflict_codes
    node_kinds = {node["kind"] for node in payload["graph"]["nodes"]}
    assert "planned_device" in node_kinds
    assert "live_device" in node_kinds
    assert "intended_config" in node_kinds
    assert "actual_config" in node_kinds
    assert "telemetry" in node_kinds
    edge_layers = {edge["layer"] for edge in payload["graph"]["edges"]}
    assert "planned_topology" in edge_layers
    assert "live_topology" in edge_layers
