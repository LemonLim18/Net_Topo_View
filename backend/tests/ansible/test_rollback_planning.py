from db.manager import DatabaseManager
from db.models import ConfigSnapshot, Device
from config_engine.rollback_planner import build_rollback_plan
from deploy.verifier import VerificationCheck, VerificationResult
from deploy.models import Job, JobStatus, PipelineStage
from deploy.pipeline import DeploymentPipeline
from deploy.rollback import RollbackEngine


def _build_db(tmp_path):
    db_path = tmp_path / "rollback_planning.db"
    db = DatabaseManager(str(db_path))
    db.initialize()
    return db


def test_rollback_plan_prefers_snapshot_and_computes_diff(tmp_path):
    db = _build_db(tmp_path)
    device_id = db.upsert_device(
        Device(hostname="SW1", management_ip="10.0.0.2", vendor="cisco_ios", status="active")
    )
    db.save_config_snapshot(
        ConfigSnapshot(
            device_id=device_id,
            config_text="hostname SW1\nvlan 10\n name legacy\n",
            captured_at="2026-03-10T10:00:00+00:00",
        )
    )

    plan = build_rollback_plan(
        db_manager=db,
        hostname="SW1",
        vendor="cisco_ios",
        feature="vlan",
        params={"vlans": [{"id": 20, "name": "Users"}]},
        generated_config="vlan 20\n name Users\n!",
        template_rollback_config="no vlan 20\n!",
    )

    assert plan["strategy"] == "snapshot_aware_diff_restore"
    assert plan["latest_snapshot"]["available"] is True
    assert plan["diff_against_snapshot"]["is_identical"] is False
    assert "runtime_pre_change_snapshot_restore" in plan["strategy_chain"]
    assert "template_reverse_commands" in plan["strategy_chain"]


def test_run_plan_stage_uses_precomputed_proposal_artifact(tmp_path):
    db = _build_db(tmp_path)
    device_id = db.upsert_device(
        Device(hostname="SW1", management_ip="10.0.0.2", vendor="cisco_ios", status="active")
    )
    pipeline = DeploymentPipeline(db)
    request_id = pipeline.create_change_request("test", "tester", "desc")
    job_id = pipeline.add_job(request_id, device_id)

    ok = pipeline.run_plan_stage(
        job_id,
        vendor="cisco_ios",
        feature="vlan",
        params={"vlans": [{"id": 20, "name": "Users"}]},
        generated_config="vlan 20\n name Users\n!",
        rollback_config="no vlan 20\n!",
        execution_engine="ansible_native",
    )

    assert ok is True
    with db._connect() as conn:
        row = conn.execute(
            "SELECT generated_config, rollback_config, execution_engine FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

    assert row["generated_config"] == "vlan 20\n name Users\n!"
    assert row["rollback_config"] == "no vlan 20\n!"
    assert row["execution_engine"] == "ansible_native"


def test_rollback_engine_prefers_snapshot_restore(tmp_path, monkeypatch):
    db = _build_db(tmp_path)
    device_id = db.upsert_device(
        Device(hostname="SW1", management_ip="10.0.0.2", vendor="cisco_ios", status="active")
    )
    snap_id = db.save_config_snapshot(
        ConfigSnapshot(
            device_id=device_id,
            config_text="hostname SW1\nvlan 10\n name legacy\n",
            captured_at="2026-03-10T10:00:00+00:00",
        )
    )

    pushed = []

    class _FakeResult:
        def __init__(self, success=True, error_message="", output="ok"):
            self.success = success
            self.error_message = error_message
            self.output = output

    class _FakeDriver:
        def connect(self):
            return None

        def disconnect(self):
            return None

        def push_config(self, commands):
            pushed.append(list(commands))
            return _FakeResult(success=True)

    monkeypatch.setattr("deploy.rollback.DriverFactory.create", lambda *args, **kwargs: _FakeDriver())

    job = Job(
        id=1,
        request_id=1,
        device_id=device_id,
        status=JobStatus.FAILED,
        stage=PipelineStage.ROLLBACK,
        snapshot_pre_id=snap_id,
        rollback_config="no vlan 20\n!",
    )

    ok = RollbackEngine(db).execute_rollback(job, host="10.0.0.2", username="u", password="p", driver_type="cisco_ios")

    assert ok is True
    assert pushed
    assert pushed[0][0] == "hostname SW1"


def test_execute_job_failure_requires_manual_rollback(tmp_path, monkeypatch):
    db = _build_db(tmp_path)
    device_id = db.upsert_device(
        Device(hostname="SW1", management_ip="10.0.0.2", vendor="cisco_ios", status="active")
    )
    pipeline = DeploymentPipeline(db)
    request_id = pipeline.create_change_request("test", "tester", "desc")
    job_id = pipeline.add_job(request_id, device_id)

    with db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET generated_config = ?, rollback_config = ?, status = ?, stage = ? WHERE id = ?",
            ("hostname SW1\ninterface Loopback0", "default interface Loopback0", JobStatus.PENDING.value, PipelineStage.PLAN.value, job_id),
        )

    class _FakeResult:
        def __init__(self, success=True, error_message="", output="ok"):
            self.success = success
            self.error_message = error_message
            self.output = output

    class _FakeDriver:
        def connect(self):
            return None

        def disconnect(self):
            return None

        def get_running_config(self):
            return "hostname SW1\n"

        def push_config(self, commands):
            return _FakeResult(success=True)

    monkeypatch.setattr("deploy.pipeline.DriverFactory.create", lambda *args, **kwargs: _FakeDriver())
    monkeypatch.setattr(
        "deploy.pipeline.Verifier.run_checks",
        lambda self, checks: [
            VerificationResult(
                check=checks[0],
                pass_=False,
                details="neighbor not established",
                raw_output="BGP Idle",
            )
        ],
    )

    ok = pipeline.execute_job(
        job_id,
        host="10.0.0.2",
        username="u",
        password="p",
        driver_type="cisco_ios",
        checks=[VerificationCheck(name="bgp", check_type="command_output", target="show bgp summary", expected="Established")],
    )

    assert ok is False
    jobs = pipeline.get_all_jobs()
    job = next(item for item in jobs if item["id"] == job_id)
    assert job["status"] == "failed"
    assert job["stage"] == "verify"

    logs = pipeline.get_job_logs(job_id)
    assert any("Automatic rollback is disabled" in log["details"] for log in logs)