import asyncio

import pytest

from config_engine.renderer import TemplateRenderer
from config_engine.safety import validate_action_artifact, validate_cli_artifact, validate_playbook_yaml
from llm.api import approve_proposal, rollback_proposal
from llm.proposal import ConfigProposal


def test_cli_validation_accepts_supported_vlan_artifact():
    params = {"vlans": [{"id": 20, "name": "Users"}]}
    renderer = TemplateRenderer()
    generated = renderer.render("cisco_ios", "vlan", dict(params))
    rollback = renderer.render_undo("cisco_ios", "vlan", dict(params))

    report = validate_cli_artifact(
        vendor="cisco_ios",
        feature="vlan",
        params=params,
        generated_config=generated,
        rollback_config=rollback,
        execution_engine="ansible_native",
        require_render_match=True,
    )

    assert report["passed"] is True
    assert report["errors"] == []


def test_cli_validation_rejects_missing_rollback_for_qos():
    params = {
        "policy_name": "EDGE_QOS",
        "class_maps": [{"name": "VOICE", "match": [{"type": "dscp", "value": "ef"}]}],
        "policy_classes": [{"class": "VOICE", "priority_percent": 30}],
        "interface": "GigabitEthernet0/1",
        "direction": "output",
    }
    generated = (
        "class-map match-any VOICE\n"
        " match dscp ef\n"
        "policy-map EDGE_QOS\n"
        " class VOICE\n"
        "  priority percent 30\n"
        "interface GigabitEthernet0/1\n"
        " service-policy output EDGE_QOS"
    )

    report = validate_cli_artifact(
        vendor="cisco_ios",
        feature="qos",
        params=params,
        generated_config=generated,
        rollback_config="",
        execution_engine="ansible_native",
        require_render_match=False,
    )

    assert report["passed"] is False
    assert any("Rollback config is missing" in error for error in report["errors"])


def test_playbook_validation_rejects_destructive_commands_without_guard():
    report = validate_playbook_yaml(
        playbook_yaml=(
            "- name: destructive\n"
            "  hosts: edge\n"
            "  gather_facts: false\n"
            "  tasks:\n"
            "    - name: bad\n"
            "      cisco.ios.ios_config:\n"
            "        commands:\n"
            "          - reload\n"
        ),
        vendor="cisco_ios",
        feature="interface",
        target_hosts=["edge"],
        execution_engine="ansible_native",
    )

    assert report["passed"] is False
    assert any("destructive" in error.lower() for error in report["errors"])


def test_approve_proposal_blocks_failed_step10_validation(monkeypatch):
    params = {"vlans": [{"id": 20, "name": "Users"}]}
    artifact = {
        "action_type": "single_device_proposal",
        "device_artifacts": [
            {
                "hostname": "SW1",
                "vendor": "cisco_ios",
                "feature": "vlan",
                "generated_config": "vlan 20\n name Users",
                "rollback_config": "no vlan 20",
                "execution_engine": "ansible_native",
            }
        ],
    }
    artifact["validation"] = validate_action_artifact(
        artifact,
        vendor="cisco_ios",
        feature="vlan",
        params=params,
    )
    proposal = ConfigProposal(
        id="proposal-1",
        status="pending",
        user_prompt="Configure VLAN 20 on SW1",
        target_device="SW1",
        target_device_id=1,
        vendor="cisco_ios",
        feature="vlan",
        params=params,
        generated_config="vlan 20\n name Users\nreload",
        rollback_config="no vlan 20",
        action_artifact=artifact,
    )

    class _Mgr:
        def get(self, proposal_id):
            return proposal if proposal_id == "proposal-1" else None

        def approve(self, proposal_id):
            raise AssertionError("approve should not be reached for invalid proposals")

    class _Agent:
        proposal_manager = _Mgr()

    monkeypatch.setattr("llm.api._agent", _Agent())

    with pytest.raises(Exception) as exc:
        asyncio.run(approve_proposal("proposal-1", user={"username": "op", "role": "operator"}))

    assert getattr(exc.value, "status_code", None) == 422


def test_snapshot_backed_rollback_counts_as_viable_for_safety():
    params = {"policy_name": "EDGE_QOS", "interface": "GigabitEthernet0/1", "direction": "output", "class_maps": [], "policy_classes": []}
    artifact = {
        "action_type": "single_device_proposal",
        "device_artifacts": [
            {
                "hostname": "SW1",
                "vendor": "cisco_ios",
                "feature": "qos",
                "generated_config": "interface GigabitEthernet0/1\n service-policy output EDGE_QOS",
                "rollback_config": "",
                "execution_engine": "ansible_native",
                "rollback_plan": {
                    "runtime_restore_available": True,
                    "strategy": "snapshot_aware_diff_restore",
                    "strategy_chain": ["runtime_pre_change_snapshot_restore"],
                },
            }
        ],
    }

    report = validate_action_artifact(
        artifact,
        vendor="cisco_ios",
        feature="qos",
        params=params,
        require_render_match=False,
    )

    assert report["passed"] is True


def test_approve_proposal_returns_post_change_findings_and_persists_artifact(monkeypatch):
    proposal = ConfigProposal(
        id="proposal-2",
        status="pending",
        user_prompt="Configure VLAN 20 on SW1",
        target_device="SW1",
        target_device_id=1,
        vendor="cisco_ios",
        feature="vlan",
        params={"vlans": [{"id": 20, "name": "Users"}]},
        generated_config="vlan 20\n name Users",
        rollback_config="no vlan 20",
        action_artifact={"execution": {"primary_engine": "ssh_fallback"}},
    )

    class _Mgr:
        def __init__(self):
            self.proposal = proposal

        def get(self, proposal_id):
            return self.proposal if proposal_id == self.proposal.id else None

        def approve(self, proposal_id):
            self.proposal.status = "approved"
            return self.proposal

        def mark_deployed(self, proposal_id, job_id=None, request_id=None):
            self.proposal.status = "deployed"
            self.proposal.job_id = job_id
            self.proposal.request_id = request_id
            return self.proposal

        def update_action_artifact(self, proposal_id, action_artifact):
            self.proposal.action_artifact = action_artifact
            return self.proposal

    class _Db:
        def get_device_by_id(self, device_id):
            class _Device:
                management_ip = "10.0.0.2"
            return _Device()

    class _Pipeline:
        db_manager = _Db()

        def create_change_request(self, title, requester, description=""):
            return 101

        def add_job(self, request_id, device_id):
            return 202

        def run_plan_stage(self, *args, **kwargs):
            return True

        def execute_job(self, **kwargs):
            return True

        def get_all_jobs(self):
            return [{"id": 202, "status": "success", "stage": "commit"}]

        def get_job_logs(self, job_id):
            return [{"stage": "verify", "details": "pass"}]

    class _Vault:
        def get(self, device_id, kind):
            return {"username": "netops", "password": "secret"}

    class _Agent:
        proposal_manager = _Mgr()

    monkeypatch.setattr("llm.api._agent", _Agent())
    monkeypatch.setattr("llm.api._pipeline", _Pipeline())
    monkeypatch.setattr("llm.api._vault", _Vault())
    monkeypatch.setattr("llm.api.validate_action_artifact", lambda *args, **kwargs: {"passed": True})
    monkeypatch.setattr(
        "llm.api.build_post_change_verification_report",
        lambda *args, **kwargs: {
            "version": "phase5_step15_v1",
            "generated_at": "2026-03-11T10:00:00+00:00",
            "outcome": "needs_review",
            "recommended_action": {"action": "correction_recommended", "reason": "telemetry stale"},
            "checks": {"telemetry_outcome": {"status": "warn"}},
        },
    )

    payload = asyncio.run(approve_proposal("proposal-2", user={"username": "admin", "role": "admin"}))

    assert payload["status"] == "deployed_with_findings"
    assert payload["post_change_verification"]["outcome"] == "needs_review"
    assert payload["recommended_action"]["action"] == "correction_recommended"
    assert proposal.action_artifact["deployment_outcome"]["status"] == "deployed"
    assert proposal.action_artifact["post_change_verification"]["outcome"] == "needs_review"


def test_approve_proposal_marks_failed_when_execution_fails(monkeypatch):
    proposal = ConfigProposal(
        id="proposal-3",
        status="pending",
        user_prompt="Configure BGP on R1",
        target_device="R1",
        target_device_id=1,
        vendor="cisco_ios",
        feature="bgp",
        params={"asn": 65001},
        generated_config="router bgp 65001",
        rollback_config="no router bgp 65001",
        action_artifact={"execution": {"primary_engine": "ssh_fallback"}},
    )

    class _Mgr:
        def __init__(self):
            self.proposal = proposal

        def get(self, proposal_id):
            return self.proposal if proposal_id == self.proposal.id else None

        def approve(self, proposal_id):
            self.proposal.status = "approved"
            return self.proposal

        def mark_failed(self, proposal_id):
            self.proposal.status = "failed"
            return self.proposal

        def update_action_artifact(self, proposal_id, action_artifact):
            self.proposal.action_artifact = action_artifact
            return self.proposal

    class _Db:
        def get_device_by_id(self, device_id):
            class _Device:
                management_ip = "10.0.0.3"
            return _Device()

    class _Pipeline:
        db_manager = _Db()

        def create_change_request(self, title, requester, description=""):
            return 303

        def add_job(self, request_id, device_id):
            return 404

        def run_plan_stage(self, *args, **kwargs):
            return True

        def execute_job(self, **kwargs):
            return False

        def get_all_jobs(self):
            return [{"id": 404, "status": "failed", "stage": "verify", "error_message": "Verification failed"}]

        def get_job_logs(self, job_id):
            return [{"stage": "rollback", "details": "rollback attempted"}]

    class _Vault:
        def get(self, device_id, kind):
            return {"username": "netops", "password": "secret"}

    class _Agent:
        proposal_manager = _Mgr()

    monkeypatch.setattr("llm.api._agent", _Agent())
    monkeypatch.setattr("llm.api._pipeline", _Pipeline())
    monkeypatch.setattr("llm.api._vault", _Vault())
    monkeypatch.setattr("llm.api.validate_action_artifact", lambda *args, **kwargs: {"passed": True})
    monkeypatch.setattr(
        "llm.api.build_post_change_verification_report",
        lambda *args, **kwargs: {
            "version": "phase5_step15_v1",
            "generated_at": "2026-03-11T10:05:00+00:00",
            "outcome": "failed",
            "recommended_action": {"action": "rollback_recommended", "reason": "verification failed and rollback data is available"},
            "checks": {"command_success": {"status": "fail"}},
        },
    )

    payload = asyncio.run(approve_proposal("proposal-3", user={"username": "admin", "role": "admin"}))

    assert payload["status"] == "failed"
    assert payload["recommended_action"]["action"] == "rollback_recommended"
    assert proposal.status == "failed"
    assert proposal.action_artifact["deployment_outcome"]["status"] == "failed"


def test_rollback_proposal_prepares_failed_proposal_without_claiming_execution(monkeypatch):
    proposal = ConfigProposal(
        id="proposal-4",
        status="failed",
        target_device="R1",
        target_device_id=0,
        vendor="cisco_ios",
        rollback_config="no router bgp 65001",
        action_artifact={"rollback_intent": {"available_device_count": 1, "strategy": "known_reverse_operations"}},
    )

    class _Mgr:
        def __init__(self):
            self.proposal = proposal

        def get(self, proposal_id):
            return self.proposal if proposal_id == self.proposal.id else None

        def update_action_artifact(self, proposal_id, action_artifact):
            self.proposal.action_artifact = action_artifact
            return self.proposal

    class _Agent:
        proposal_manager = _Mgr()

    monkeypatch.setattr("llm.api._agent", _Agent())
    monkeypatch.setattr("llm.api._pipeline", None)

    payload = asyncio.run(rollback_proposal("proposal-4", user={"username": "admin", "role": "admin"}))

    assert payload["status"] == "rollback_prepared"
    assert proposal.status == "failed"
    assert proposal.action_artifact["rollback_execution"]["status"] == "prepared"