import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ansible import api as ansible_api


class _FakeBuilder:
    async def build_draft(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("Builder should not be invoked for interface disambiguation failures")

    def validate_playbook(self, *args, **kwargs):
        return {"passed": True, "issues": [], "errors": []}


def _install_deploy_models_stub():
    # Stub deploy.models so importing create_and_plan doesn't pull optional deps (e.g. paramiko).
    deploy_mod = types.ModuleType("deploy")
    deploy_models_mod = types.ModuleType("deploy.models")

    class ChangeRequest:
        def __init__(self, title, description, requester, owner_username=None, **kwargs):
            self.title = title
            self.description = description
            self.requester = requester
            self.owner_username = owner_username

    class Job:
        def __init__(self, request_id, device_id, owner_username=None, **kwargs):
            self.request_id = request_id
            self.device_id = device_id
            self.owner_username = owner_username

    class DeploymentRepository:
        def __init__(self, db_manager):
            self.db_manager = db_manager

        def create_request(self, change_request):
            return 12

        def create_job(self, job):
            return 34

    deploy_models_mod.ChangeRequest = ChangeRequest
    deploy_models_mod.Job = Job
    deploy_models_mod.DeploymentRepository = DeploymentRepository
    deploy_mod.models = deploy_models_mod

    sys.modules["deploy"] = deploy_mod
    sys.modules["deploy.models"] = deploy_models_mod
    return deploy_mod, deploy_models_mod


class TestCreateAndPlanInterfaceDisambiguation(unittest.TestCase):
    def _run_case(self, *, feature: str, params: dict, intent_description: str):
        old_deploy = sys.modules.get("deploy")
        old_deploy_models = sys.modules.get("deploy.models")
        deploy_mod, deploy_models_mod = _install_deploy_models_stub()

        old_db = ansible_api._db
        old_pipeline = ansible_api._pipeline
        old_builder = ansible_api._builder
        old_agent = ansible_api._agent

        try:
            ansible_api._db = SimpleNamespace(get_all_devices=lambda: [])
            ansible_api._pipeline = object()
            ansible_api._builder = _FakeBuilder()
            ansible_api._agent = None

            planning_context = {
                "devices": [
                    {
                        "hostname": "SW1",
                        "management_ip": "10.0.0.1",
                        "vendor": "cisco_ios",
                        "role": "access",
                        "site": "",
                        "interfaces": [{"name": "Gi0/1"}, {"name": "Gi0/2"}, {"name": "Gi0/3"}],
                        "neighbors": [],
                    }
                ]
            }

            async def _fake_ensure_planning_context(**kwargs):
                return planning_context, {"reconciled": False, "issues": []}

            def _fake_resolve_backend_intent_request(req, user):
                plan_req = ansible_api.PlanAnsibleRequest(
                    intent_description=req.intent_description,
                    target_hosts=["SW1"],
                    vendor="cisco_ios",
                    feature=feature,
                    params=params,
                    auto_refine=False,
                )
                parsed_intent = {"feature": feature, "resolved_devices": ["SW1"]}
                policy = {"approved": True}
                return plan_req, parsed_intent, policy

            with patch.object(ansible_api, "_ensure_planning_context", side_effect=_fake_ensure_planning_context):
                with patch.object(ansible_api, "_resolve_backend_intent_request", side_effect=_fake_resolve_backend_intent_request):
                    with self.assertRaises(Exception) as ctx:
                        asyncio.run(
                            ansible_api.create_and_plan(
                                ansible_api.CreateAndPlanRequest(
                                    intent_description=intent_description,
                                    device_id=99,
                                ),
                                user={"username": "tester", "role": "admin"},
                            )
                        )

            e = ctx.exception
            self.assertEqual(getattr(e, "status_code", None), 422)
            detail = getattr(e, "detail", {}) or {}
            self.assertIn("interfaces_disambiguation", detail)
            inv = detail.get("invalid_entities") or []
            self.assertTrue(any(x.get("kind") == "interface" for x in inv))
        finally:
            # Restore modules / globals
            if sys.modules.get("deploy") is deploy_mod:
                del sys.modules["deploy"]
            if sys.modules.get("deploy.models") is deploy_models_mod:
                del sys.modules["deploy.models"]
            if old_deploy is not None:
                sys.modules["deploy"] = old_deploy
            if old_deploy_models is not None:
                sys.modules["deploy.models"] = old_deploy_models

            ansible_api._db = old_db
            ansible_api._pipeline = old_pipeline
            ansible_api._builder = old_builder
            ansible_api._agent = old_agent

    def test_acl_invalid_apply_to_triggers_interface_disambiguation(self):
        self._run_case(
            feature="acl",
            intent_description="Configure ACL TEST inbound on Gi0/99",
            params={
                "acl_name": "TEST",
                "acl_type": "ipv4",
                "rules": ["permit ip any any"],
                "apply_to": ["Gi0/99 in"],
            },
        )

    def test_qos_invalid_interface_apply_triggers_interface_disambiguation(self):
        self._run_case(
            feature="qos",
            intent_description="Configure QoS policy P1 on Gi0/99 out",
            params={
                "policy_name": "P1",
                "qos_type": "class-map",
                "classification_rules": ["match any"],
                "interface_apply": ["Gi0/99 out"],
            },
        )

    def test_ospf_invalid_passive_interfaces_triggers_interface_disambiguation(self):
        self._run_case(
            feature="ospf",
            intent_description="Configure OSPF 1 with passive Gi0/99",
            params={
                "process_id": 1,
                "networks": ["10.0.0.0/24"],
                "passive_interfaces": ["Gi0/99"],
            },
        )

    def test_static_route_invalid_next_hop_interface_triggers_interface_disambiguation(self):
        self._run_case(
            feature="static_route",
            intent_description="Add static route 0.0.0.0/0 via Gi0/99",
            params={
                "destination": "0.0.0.0/0",
                "next_hop": "Gi0/99",
            },
        )


if __name__ == "__main__":
    unittest.main()

