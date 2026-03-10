import asyncio

import pytest

from config_engine.renderer import TemplateRenderer
from config_engine.safety import validate_action_artifact, validate_cli_artifact, validate_playbook_yaml
from llm.api import approve_proposal
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