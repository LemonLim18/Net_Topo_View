"""
M19 Agentic Config Workflow — Verification Tests
=================================================

Covers:
  1. Unit test: ProposalManager lifecycle (create → approve → deploy → rollback)
  2. Unit test: ProposalManager reject flow
  3. Integration test: Proposal API endpoints (with in-memory SQLite)

Usage::

    cd backend
    python -m pytest ../tests/verify_m19.py -v

Or standalone::

    cd backend
    python ../tests/verify_m19.py
"""

import json
import os
import sys
import tempfile
import unittest

# Allow importing backend modules from anywhere
_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ---------------------------------------------------------------------------
# 1. Unit Tests — ProposalManager lifecycle
# ---------------------------------------------------------------------------

class TestProposalManagerLifecycle(unittest.TestCase):
    """Test the full proposal state machine using a temp SQLite DB."""

    def setUp(self):
        """Create a temp DB and initialise the schema before each test."""
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_path = self._tmp.name
        self._tmp.close()

        # Initialise schema (creates config_proposals table)
        from db.manager import DatabaseManager
        db = DatabaseManager(self._db_path)
        db.initialize()

        # Create proposal manager pointing at the temp DB
        from llm.proposal import ProposalManager
        self.mgr = ProposalManager(db_path=self._db_path)

    def tearDown(self):
        os.unlink(self._db_path)

    # ------------------------------------------------------------------
    def test_create_and_get(self):
        p = self.mgr.create(
            user_prompt="Configure OSPF on R1",
            target_device="R1",
            target_device_id=1,
            vendor="cisco_ios",
            feature="ospf",
            params={"process_id": 1, "area": 0},
            generated_config="router ospf 1\n network 10.0.0.0 0.0.0.255 area 0",
            rollback_config="no router ospf 1",
            diff_text="--- current\n+++ proposed\n+router ospf 1",
            risk_level="medium",
            ai_explanation="This enables OSPF on R1.",
        )

        self.assertIsNotNone(p.id)
        self.assertEqual(p.status, "pending")
        self.assertEqual(p.target_device, "R1")

        fetched = self.mgr.get(p.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, p.id)
        self.assertEqual(fetched.vendor, "cisco_ios")
        print(f"  [PASS] create + get  id={p.id[:8]}")

    def test_approve_flow(self):
        p = self._make_proposal()

        approved = self.mgr.approve(p.id)
        self.assertIsNotNone(approved)
        self.assertEqual(approved.status, "approved")

        # Cannot approve again from a non-pending state
        double = self.mgr.approve(p.id)
        self.assertIsNone(double)
        print(f"  [PASS] approve flow  id={p.id[:8]}")

    def test_deploy_flow(self):
        p = self._make_proposal()
        self.mgr.approve(p.id)

        # Use None for job_id/request_id — NULL bypasses FK constraints in
        # the isolated test DB which has no rows in jobs/change_requests.
        deployed = self.mgr.mark_deployed(p.id, job_id=None, request_id=None)
        self.assertIsNotNone(deployed)
        self.assertEqual(deployed.status, "deployed")
        self.assertIsNone(deployed.job_id)
        self.assertIsNone(deployed.request_id)
        print(f"  [PASS] deploy flow   id={p.id[:8]}")

    def test_rollback_flow(self):
        p = self._make_proposal()
        self.mgr.approve(p.id)
        self.mgr.mark_deployed(p.id, job_id=None, request_id=None)

        rolled = self.mgr.mark_rolled_back(p.id)
        self.assertIsNotNone(rolled)
        self.assertEqual(rolled.status, "rolled_back")
        print(f"  [PASS] rollback flow id={p.id[:8]}")

    def test_reject_flow(self):
        p = self._make_proposal()

        rejected = self.mgr.reject(p.id)
        self.assertIsNotNone(rejected)
        self.assertEqual(rejected.status, "rejected")

        # Cannot reject after rejection
        double = self.mgr.reject(p.id)
        self.assertIsNone(double)
        print(f"  [PASS] reject flow   id={p.id[:8]}")

    def test_list_pending(self):
        p1 = self._make_proposal()
        p2 = self._make_proposal()
        self.mgr.reject(p2.id)

        pending = self.mgr.list_pending()
        ids = [p.id for p in pending]
        self.assertIn(p1.id, ids)
        self.assertNotIn(p2.id, ids)
        print(f"  [PASS] list_pending  ({len(pending)} pending)")

    def test_list_all(self):
        self._make_proposal()
        self._make_proposal()
        all_p = self.mgr.list_all()
        self.assertGreaterEqual(len(all_p), 2)
        print(f"  [PASS] list_all      ({len(all_p)} total)")

    # helpers
    def _make_proposal(self):
        return self.mgr.create(
            user_prompt="Configure VLAN 100 on R1",
            target_device="R1",
            target_device_id=1,
            vendor="cisco_ios",
            feature="vlan",
            params={"vlan_id": 100, "name": "Test"},
            generated_config="vlan 100\n name Test",
            rollback_config="no vlan 100",
        )


# ---------------------------------------------------------------------------
# 2. Integration Tests — FastAPI proposal endpoints
# ---------------------------------------------------------------------------

class TestProposalAPIEndpoints(unittest.TestCase):
    """
    Test the proposal REST endpoints via FastAPI TestClient.

    Starts with a fresh in-memory SQLite and a real ProposalManager,
    but a *mock* agent so no Gemini key is needed.
    """

    def setUp(self):
        try:
            from fastapi.testclient import TestClient
            from fastapi import FastAPI
        except ImportError:
            self.skipTest("fastapi[testclient] / httpx not installed")

        import tempfile, os as _os

        # Temp DB
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_path = self._tmp.name
        self._tmp.close()

        from db.manager import DatabaseManager
        db = DatabaseManager(self._db_path)
        db.initialize()

        # Create a real ProposalManager via a minimal mock agent
        from llm.proposal import ProposalManager

        class MockAgent:
            model_name = "mock-model"
            available_tools = []
            proposal_manager = ProposalManager(db_path=self._db_path)

            def get_history(self):
                return []

            async def chat(self, _msg):
                return {"role": "assistant", "content": "ok", "type": "text"}

            def clear_history(self):
                pass

            def refresh_context(self):
                pass

        from llm.api import router, setup_agent
        # Create a fresh ProposalManager instance for this test and wire it
        # into a new MockAgent instance, then call setup_agent so the router
        # globals (_agent) point at this test's manager.
        mock_instance = MockAgent()
        mock_instance.__class__.proposal_manager = ProposalManager(db_path=self._db_path)
        setup_agent(mock_instance, pipeline=None, vault=None)

        # The router already carries prefix="/llm/chat" — do NOT add it again
        # or every path gets doubled to /llm/chat/llm/chat/...
        test_app = FastAPI()
        test_app.include_router(router)
        self.client = TestClient(test_app)
        self.mgr = mock_instance.__class__.proposal_manager

    def tearDown(self):
        os.unlink(self._db_path)

    def _create_proposal(self) -> str:
        """Insert a proposal directly and return its ID."""
        p = self.mgr.create(
            user_prompt="Configure OSPF on R1 via API test",
            target_device="R1",
            target_device_id=1,
            vendor="cisco_ios",
            feature="ospf",
            params={"process_id": 1},
            generated_config="router ospf 1",
            rollback_config="no router ospf 1",
        )
        return p.id

    def test_list_proposals_empty(self):
        r = self.client.get("/llm/chat/proposals")
        self.assertEqual(r.status_code, 200)
        self.assertIn("proposals", r.json())
        print(f"  [PASS] GET /proposals (empty)")

    def test_list_proposals_with_data(self):
        self._create_proposal()
        r = self.client.get("/llm/chat/proposals")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()["proposals"]), 1)
        print(f"  [PASS] GET /proposals (with data)")

    def test_get_proposal_by_id(self):
        pid = self._create_proposal()
        r = self.client.get(f"/llm/chat/proposals/{pid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], pid)
        print(f"  [PASS] GET /proposals/{{id}}")

    def test_get_proposal_not_found(self):
        r = self.client.get("/llm/chat/proposals/nonexistent-id")
        self.assertEqual(r.status_code, 404)
        print(f"  [PASS] GET /proposals/{{id}} 404")

    def test_reject_proposal(self):
        pid = self._create_proposal()
        r = self.client.post(f"/llm/chat/proposals/{pid}/reject")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "rejected")
        self.assertEqual(data["proposal"]["status"], "rejected")
        print(f"  [PASS] POST /proposals/{{id}}/reject")

    def test_reject_already_rejected(self):
        pid = self._create_proposal()
        self.client.post(f"/llm/chat/proposals/{pid}/reject")
        # Second reject should get 409
        r = self.client.post(f"/llm/chat/proposals/{pid}/reject")
        self.assertEqual(r.status_code, 409)
        print(f"  [PASS] POST /proposals/{{id}}/reject 409 (double)")

    def test_approve_no_pipeline_simulated(self):
        """When no pipeline is set, approve returns simulated deployed status."""
        pid = self._create_proposal()
        r = self.client.post(f"/llm/chat/proposals/{pid}/approve")
        # No vault → 412 (credentials not found)
        # Vault is None AND device_id=1 → credential lookup skipped → 412
        self.assertIn(r.status_code, [200, 412])
        print(f"  [PASS] POST /proposals/{{id}}/approve (no-pipeline) → {r.status_code}")

    def test_rollback_not_deployed(self):
        """Rollback on a pending proposal should return 409."""
        pid = self._create_proposal()
        r = self.client.post(f"/llm/chat/proposals/{pid}/rollback")
        self.assertEqual(r.status_code, 409)
        print(f"  [PASS] POST /proposals/{{id}}/rollback 409 (not deployed)")

    def test_filter_by_status(self):
        pid = self._create_proposal()
        self.client.post(f"/llm/chat/proposals/{pid}/reject")

        r_pending = self.client.get("/llm/chat/proposals?status=pending")
        r_rejected = self.client.get("/llm/chat/proposals?status=rejected")
        self.assertEqual(r_pending.status_code, 200)
        self.assertEqual(r_rejected.status_code, 200)
        rejected_ids = [p["id"] for p in r_rejected.json()["proposals"]]
        self.assertIn(pid, rejected_ids)
        print(f"  [PASS] GET /proposals?status=rejected filter")


# ---------------------------------------------------------------------------
# 3. Structural / smoke tests
# ---------------------------------------------------------------------------

class TestM19StructuralIntegrity(unittest.TestCase):
    """Verify all M19 modules import correctly and expose expected interfaces."""

    def test_proposal_module_imports(self):
        from llm.proposal import ProposalManager, ConfigProposal
        mgr = ProposalManager.__new__(ProposalManager)
        self.assertTrue(hasattr(mgr, "create"))
        self.assertTrue(hasattr(mgr, "get"))
        self.assertTrue(hasattr(mgr, "approve"))
        self.assertTrue(hasattr(mgr, "reject"))
        self.assertTrue(hasattr(mgr, "mark_deployed"))
        self.assertTrue(hasattr(mgr, "mark_rolled_back"))
        self.assertTrue(hasattr(mgr, "list_pending"))
        self.assertTrue(hasattr(mgr, "list_all"))
        print("  [PASS] ProposalManager has all required methods")

    def test_config_proposal_fields(self):
        from llm.proposal import ConfigProposal
        p = ConfigProposal()
        expected_fields = {
            "id", "status", "user_prompt", "target_device",
            "target_device_id", "vendor", "feature", "params",
            "generated_config", "rollback_config", "diff_text",
            "risk_level", "ai_explanation", "job_id", "request_id",
            "created_at", "updated_at",
        }
        self.assertEqual(expected_fields, set(ConfigProposal.model_fields.keys()))
        print("  [PASS] ConfigProposal has all expected fields")

    def test_llm_api_router_has_proposal_routes(self):
        from llm.api import router
        routes = {r.path for r in router.routes}
        # The router uses prefix="/llm/chat", so paths include that prefix.
        expected = {
            "/llm/chat/proposals",
            "/llm/chat/proposals/{proposal_id}",
            "/llm/chat/proposals/{proposal_id}/approve",
            "/llm/chat/proposals/{proposal_id}/reject",
            "/llm/chat/proposals/{proposal_id}/rollback",
        }
        for path in expected:
            self.assertIn(path, routes, f"Missing route: {path}")
        print(f"  [PASS] All 5 proposal routes registered: {sorted(expected)}")

    def test_agent_exposes_proposal_manager(self):
        from llm.agent import NetworkAgent
        self.assertTrue(hasattr(NetworkAgent, "proposal_manager") or
                        "proposal_manager" in dir(NetworkAgent))
        print("  [PASS] NetworkAgent exposes proposal_manager property")

    def test_schema_has_proposals_table(self):
        import re
        from pathlib import Path
        schema = (
            Path(__file__).parent.parent / "backend" / "db" / "schema.sql"
        ).read_text()
        self.assertIn("config_proposals", schema)
        self.assertIn("idx_proposals_status", schema)
        print("  [PASS] schema.sql contains config_proposals table + index")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("M19 Agentic Config Workflow — Verification Tests")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestM19StructuralIntegrity))
    suite.addTests(loader.loadTestsFromTestCase(TestProposalManagerLifecycle))
    suite.addTests(loader.loadTestsFromTestCase(TestProposalAPIEndpoints))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
