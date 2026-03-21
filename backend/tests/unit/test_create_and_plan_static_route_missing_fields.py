import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ansible import api as ansible_api


class _FakeBuilder:
    async def build_draft(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("LLM builder should not be invoked when static_route fields are missing")

    def validate_playbook(self, *args, **kwargs):
        return {"passed": True, "issues": [], "errors": []}


class TestCreateAndPlanStaticRouteMissingFields(unittest.TestCase):
    def test_create_and_plan_static_route_asks_questions_before_generation(self):
        old_db = getattr(ansible_api, "_db", None)
        old_pipeline = getattr(ansible_api, "_pipeline", None)
        old_builder = getattr(ansible_api, "_builder", None)
        old_agent = getattr(ansible_api, "_agent", None)

        ansible_api._db = SimpleNamespace(get_all_devices=lambda: [])
        ansible_api._pipeline = object()
        ansible_api._builder = _FakeBuilder()
        ansible_api._agent = None

        def _fake_resolve_backend_intent_request(req, user):
            plan_req = ansible_api.PlanAnsibleRequest(
                intent_description=req.intent_description,
                target_hosts=["SW1"],
                vendor="cisco_ios",
                feature="static_route",
                params={"destination": "", "next_hop": None},
                auto_refine=False,
            )
            parsed_intent = {"feature": "static_route", "resolved_devices": ["SW1"]}
            policy = {"approved": True}
            return plan_req, parsed_intent, policy

        class _FakeRepo:
            def __init__(self, db_manager):
                self.db_manager = db_manager

            def create_request(self, change_request):
                return 12

            def create_job(self, job):
                return 34

        try:
            # Stub deploy.models to avoid importing the real deploy package
            # (which pulls optional deps like paramiko in this environment).
            old_deploy = sys.modules.get("deploy")
            old_deploy_models = sys.modules.get("deploy.models")
            deploy_mod = types.ModuleType("deploy")
            deploy_models_mod = types.ModuleType("deploy.models")

            class ChangeRequest:
                def __init__(self, title, description, requester):
                    self.title = title
                    self.description = description
                    self.requester = requester

            class Job:
                def __init__(self, request_id, device_id):
                    self.request_id = request_id
                    self.device_id = device_id

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

            with patch.object(
                ansible_api,
                "_resolve_backend_intent_request",
                side_effect=_fake_resolve_backend_intent_request,
            ):
                async def _fake_ensure_planning_context(*args, **kwargs):
                    return {}, {"reconciled": False, "issues": []}

                with patch.object(
                    ansible_api,
                    "_ensure_planning_context",
                    side_effect=_fake_ensure_planning_context,
                ):
                    with self.assertRaises(Exception) as ctx:
                        asyncio.run(
                            ansible_api.create_and_plan(
                                ansible_api.CreateAndPlanRequest(
                                    intent_description="Add a default static route",
                                    device_id=99,
                                ),
                                user={"username": "tester", "role": "admin"},
                            )
                        )

            e = ctx.exception
            self.assertEqual(getattr(e, "status_code", None), 422)
            detail = getattr(e, "detail", {}) or {}
            missing = detail.get("missing_fields") or []
            questions = detail.get("questions") or []
            self.assertIn("destination", missing)
            self.assertIn("next_hop", missing)
            self.assertTrue(any(q.get("field") == "destination" for q in questions))
            self.assertTrue(any(q.get("field") == "next_hop" for q in questions))
        finally:
            if "deploy" in sys.modules and sys.modules.get("deploy") is deploy_mod:
                del sys.modules["deploy"]
            if "deploy.models" in sys.modules and sys.modules.get("deploy.models") is deploy_models_mod:
                del sys.modules["deploy.models"]
            if old_deploy is not None:
                sys.modules["deploy"] = old_deploy
            if old_deploy_models is not None:
                sys.modules["deploy.models"] = old_deploy_models
            ansible_api._db = old_db
            ansible_api._pipeline = old_pipeline
            ansible_api._builder = old_builder
            ansible_api._agent = old_agent


if __name__ == "__main__":
    unittest.main()

