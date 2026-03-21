from pathlib import Path

from db.manager import DatabaseManager
from db.models import ConfigSnapshot, Device, DriftEvent
from deploy.models import ChangeRequest, DeploymentRepository, Job, JobStatus, PipelineStage
from incidents.store import IncidentStore
from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_DRIFT,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_TELEMETRY,
)
from llm.context_state import get_context_state_manager
from monitor import threshold_engine


def _version(domain: str) -> int:
    return int(get_context_state_manager().snapshot()["versions"][domain])


def _make_db(tmp_path: Path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "netauto_test.db"))
    db.initialize()
    return db


def test_config_snapshot_and_drift_writes_invalidate_context_versions(tmp_path):
    db = _make_db(tmp_path)

    device_id = db.upsert_device(Device(hostname="R1", management_ip="10.0.0.1"))

    config_before = _version(CONTEXT_DOMAIN_CONFIG)
    snapshot_id = db.save_config_snapshot(
        ConfigSnapshot(device_id=device_id, config_text="hostname R1")
    )
    assert snapshot_id > 0
    assert _version(CONTEXT_DOMAIN_CONFIG) == config_before + 1

    drift_before = _version(CONTEXT_DOMAIN_DRIFT)
    drift_id = db.save_drift_event(
        DriftEvent(
            device_id=device_id,
            hostname="R1",
            has_drift=True,
            severity="high",
            diff_text="- old\n+ new",
        )
    )
    assert drift_id > 0
    assert _version(CONTEXT_DOMAIN_DRIFT) == drift_before + 1


def test_incident_store_mutations_invalidate_incident_context(tmp_path):
    db_path = tmp_path / "incident_test.db"
    store = IncidentStore(str(db_path))

    before_create = _version(CONTEXT_DOMAIN_INCIDENTS)
    incident_id = store.create(
        anomaly_type="packet_loss",
        severity="high",
        affected_devices=["SW1"],
        metrics={"current": 85.0, "threshold": 50.0},
    )
    assert _version(CONTEXT_DOMAIN_INCIDENTS) == before_create + 1

    before_update = _version(CONTEXT_DOMAIN_INCIDENTS)
    assert store.update_analysis(
        incident_id,
        logs=["loss observed"],
        telemetry={"packet_loss": 85.0},
        root_cause_summary="uplink issue",
        confidence=0.8,
    )
    assert _version(CONTEXT_DOMAIN_INCIDENTS) == before_update + 1

    before_status = _version(CONTEXT_DOMAIN_INCIDENTS)
    assert store.set_status(incident_id, "resolved", approver="user")
    assert _version(CONTEXT_DOMAIN_INCIDENTS) == before_status + 1


def test_deployment_status_update_invalidates_live_context(tmp_path):
    db = _make_db(tmp_path)
    repo = DeploymentRepository(db)
    device_id = db.upsert_device(Device(hostname="SW1", management_ip="10.0.0.2"))

    request_id = repo.create_request(ChangeRequest(title="Deploy VLAN", requester="tester"))
    job_id = repo.create_job(Job(request_id=request_id, device_id=device_id))

    before = _version(CONTEXT_DOMAIN_LIVE_TOPOLOGY)
    repo.update_job_status(job_id, JobStatus.RUNNING, PipelineStage.APPLY)
    assert _version(CONTEXT_DOMAIN_LIVE_TOPOLOGY) == before + 1


def test_threshold_breach_invalidates_telemetry_context(tmp_path):
    db = _make_db(tmp_path)
    threshold_engine.setup_thresholds(db)

    with db._connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO threshold_rules
                (name, metric, operator, threshold, severity, enabled, auto_remediate)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("CPU high", "cpu_usage", ">", 80.0, "critical", 1, 0),
        )
        rule_id = cur.lastrowid

    before = _version(CONTEXT_DOMAIN_TELEMETRY)
    threshold_engine._maybe_create_alert(
        {
            "id": rule_id,
            "name": "CPU high",
            "metric": "cpu_usage",
            "operator": ">",
            "threshold": 80.0,
            "severity": "critical",
            "cooldown_minutes": 15,
            "auto_remediate": 0,
        },
        {
            "device": "SW1",
            "metric": "cpu_usage",
            "value": 91.0,
        },
    )
    assert _version(CONTEXT_DOMAIN_TELEMETRY) == before + 1