import json

from llm import post_change_verification as verification
from llm.proposal import ConfigProposal


def test_build_post_change_verification_report_returns_passed_outcome(monkeypatch):
    proposal = ConfigProposal(
        id="proposal-1",
        target_device="R1",
        generated_config="interface Loopback0\n description test",
        rollback_config="default interface Loopback0",
    )

    class _Pipeline:
        def get_all_jobs(self):
            return [{"id": 22, "status": "success", "stage": "commit", "error_message": None}]

        def get_job_logs(self, job_id):
            return [{"stage": "verify", "details": "all checks passed"}]

    monkeypatch.setattr(
        verification.network_tools,
        "get_device_running_config",
        lambda hostname: json.dumps({"hostname": hostname, "config": "interface Loopback0\n description test", "snapshot_time": "2026-03-11T09:59:00+00:00"}),
    )
    monkeypatch.setattr(
        verification.network_tools,
        "get_device_telemetry_snapshot",
        lambda hostname: json.dumps({"hostname": hostname, "total": 4, "source_metadata": {"freshness_class": "live"}}),
    )
    monkeypatch.setattr(
        verification.network_tools,
        "get_recent_incidents",
        lambda limit=20, status="": json.dumps({"incidents": []}),
    )
    monkeypatch.setattr(
        verification.network_tools,
        "get_recent_drift_events",
        lambda limit=20, hostname="": json.dumps({"events": []}),
    )

    report = verification.build_post_change_verification_report(proposal, job_id=22, request_id=11, pipeline=_Pipeline())

    assert report["outcome"] == "passed"
    assert report["recommended_action"]["action"] == "monitor"
    assert report["checks"]["config_presence"]["status"] == "pass"
    assert report["checks"]["command_success"]["status"] == "pass"


def test_build_post_change_verification_report_recommends_rollback_on_failed_checks(monkeypatch):
    proposal = ConfigProposal(
        id="proposal-2",
        target_device="R2",
        generated_config="router bgp 65001\n neighbor 10.0.0.1 remote-as 65002",
        rollback_config="no router bgp 65001",
    )

    class _Pipeline:
        def get_all_jobs(self):
            return [{"id": 33, "status": "failed", "stage": "verify", "error_message": "Verification failed"}]

        def get_job_logs(self, job_id):
            return [{"stage": "verify", "details": "bgp neighbor did not establish"}]

    monkeypatch.setattr(
        verification.network_tools,
        "get_device_running_config",
        lambda hostname: json.dumps({"hostname": hostname, "config": "router bgp 65001", "snapshot_time": "2026-03-11T10:02:00+00:00"}),
    )
    monkeypatch.setattr(
        verification.network_tools,
        "get_device_telemetry_snapshot",
        lambda hostname: json.dumps({"hostname": hostname, "total": 0, "source_metadata": {"freshness_class": "stale"}}),
    )
    monkeypatch.setattr(
        verification.network_tools,
        "get_recent_incidents",
        lambda limit=20, status="": json.dumps({"incidents": [{"id": "INC-1", "status": "open", "affected_devices": json.dumps(["R2"])}]}),
    )
    monkeypatch.setattr(
        verification.network_tools,
        "get_recent_drift_events",
        lambda limit=20, hostname="": json.dumps({"events": [{"hostname": hostname, "has_drift": True, "severity": "high"}]}),
    )

    report = verification.build_post_change_verification_report(proposal, job_id=33, request_id=12, pipeline=_Pipeline())

    assert report["outcome"] == "failed"
    assert report["recommended_action"]["action"] == "rollback_recommended"
    assert "config_presence" in report["failed_checks"]
    assert "incident_impact" in report["failed_checks"]
    assert report["rollback_recommendation"]["recommended"] is True