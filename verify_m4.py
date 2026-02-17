"""
Verification script for M4 Deployment Pipeline.
Mocks the network driver to test the full lifecycle:
Create Request -> Plan -> Execute (Snapshot, Apply, Verify, Commit).
"""

import sys
import unittest
from unittest.mock import MagicMock, patch
import json
import logging
import traceback
import os

# Add project root to path
sys.path.insert(0, ".")

# Setup logging
logging.basicConfig(level=logging.INFO)



from backend.db.manager import DatabaseManager
from backend.deploy.pipeline import DeploymentPipeline
from backend.deploy.models import JobStatus, PipelineStage, RequestStatus
from backend.deploy.verifier import VerificationCheck

class TestM4Pipeline(unittest.TestCase):
    
    def setUp(self):
        # Use temp file DB for testing because :memory: is cleared on close
        self.db_file = "test_m4.db"
        if os.path.exists(self.db_file):
            os.remove(self.db_file)
            
        self.db = DatabaseManager(self.db_file)
        self.pipeline = DeploymentPipeline(self.db)
        
        # Initialize schema (M14 + M4 tables)
        with self.db._connect() as conn:
            # Create M14 tables
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS devices (id INTEGER PRIMARY KEY, hostname TEXT, status TEXT, management_ip TEXT);
                CREATE TABLE IF NOT EXISTS config_snapshots (id INTEGER PRIMARY KEY, device_id INTEGER, config_text TEXT, config_hash TEXT, captured_at TEXT);
                CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, user_id TEXT, action TEXT, target TEXT, details TEXT, created_at TEXT);
            """)
            # Create M4 tables manually since schema.sql isn't automatically applied to :memory:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS change_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT, status TEXT DEFAULT 'draft', requester TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, device_id INTEGER, 
                    status TEXT DEFAULT 'pending', stage TEXT, snapshot_pre_id INTEGER, snapshot_post_id INTEGER, 
                    generated_config TEXT, rollback_config TEXT, error_message TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS job_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, stage TEXT, status TEXT DEFAULT 'running', details TEXT, created_at TEXT DEFAULT (datetime('now'))
                );
            """)
            
            # Seed a device
            conn.execute("INSERT INTO devices (hostname, status, management_ip) VALUES ('R1', 'up', '192.168.1.1')")
            self.device_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def tearDown(self):
        if os.path.exists(self.db_file):
            # os.remove(self.db_file) # Keep for debugging if needed, or remove
            try:
                os.remove(self.db_file)
            except PermissionError:
                pass # Already open?

    @patch("backend.deploy.pipeline.generate_config")
    @patch("backend.deploy.pipeline.generate_rollback")
    @patch("backend.drivers.factory.DriverFactory.create")
    def test_full_pipeline_success(self, mock_create_driver, mock_gen_rollback, mock_gen_config):
        print("\n>>> Testing Full M4 Pipeline (Happy Path)")
        
        # 1. Setup Mocks
        # Mock M3 Config Engine
        mock_gen_config.return_value = json.dumps({"config": "interface VLAN10\n description TEST"})
        mock_gen_rollback.return_value = json.dumps({"rollback_config": "no interface VLAN10"})
        
        # Mock M1 Driver
        mock_driver = MagicMock()
        mock_driver.get_running_config.return_value = "hostname R1\n!"
        mock_driver.push_config.return_value.success = True
        mock_driver.push_config.return_value.output = "Config applied"
        mock_driver.run_command.return_value = "!!!" # Connectivity check pass
        mock_driver.is_alive.return_value = True
        
        mock_create_driver.return_value = mock_driver

        # 2. Create Request
        req_id = self.pipeline.create_change_request("Test VLAN", "alice")
        print(f"[PASS] Created Request {req_id}")
        
        # 3. Add Job
        job_id = self.pipeline.add_job(req_id, self.device_id)
        print(f"[PASS] Added Job {job_id}")

        # 4. Plan Stage
        print("... Running PLAN stage")
        success = self.pipeline.run_plan_stage(
            job_id, "cisco_ios", "vlan", {"vlans": [{"id": 10}]}
        )
        self.assertTrue(success)
        
        # Verify DB state
        with self.db._connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            self.assertEqual(job["status"], JobStatus.PENDING)
            self.assertEqual(job["stage"], PipelineStage.PLAN)
            self.assertIn("interface VLAN10", job["generated_config"])
            print("[PASS] PLAN stage verified (DB updated)")

        # 5. Execute Stage (Apply + Verify)
        print("... Running EXECUTE stage")
        checks = [VerificationCheck(name="Ping Check", check_type="connectivity", target="8.8.8.8")]
        
        success = self.pipeline.execute_job(
            job_id, host="1.2.3.4", username="u", password="p", checks=checks
        )
        self.assertTrue(success)
        
        # Verify Driver Calls
        mock_driver.connect.assert_called_once()
        mock_driver.get_running_config.assert_called() # Snapshot Pre & Post
        mock_driver.push_config.assert_called_once()   # content should match generated config
        mock_driver.run_command.assert_called()        # Verification
        mock_driver.disconnect.assert_called_once()
        print("[PASS] Driver interaction verified")

        # Verify DB final state
        with self.db._connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            self.assertEqual(job["status"], JobStatus.SUCCESS)
            self.assertEqual(job["stage"], PipelineStage.COMMIT)
            self.assertIsNotNone(job["snapshot_pre_id"])
            self.assertIsNotNone(job["snapshot_post_id"])
            print("[PASS] Job success verified in DB")

    @patch("backend.deploy.pipeline.generate_config")
    @patch("backend.deploy.pipeline.generate_rollback")
    @patch("backend.drivers.factory.DriverFactory.create")
    def test_pipeline_verification_failure_rollback(self, mock_create_driver, mock_gen_rollback, mock_gen_config):
        print("\n>>> Testing M4 Pipeline (Verification Failure -> Rollback)")
        
        # Mocks
        mock_gen_config.return_value = json.dumps({"config": "bad config"})
        mock_gen_rollback.return_value = json.dumps({"rollback_config": "undo bad config"})
        
        mock_driver = MagicMock()
        mock_driver.get_running_config.return_value = "hostname R1"
        mock_driver.push_config.return_value.success = True
        mock_driver.run_command.return_value = "Destination unreachable" # Ping fail
        
        mock_create_driver.return_value = mock_driver

        # Create Job
        req_id = self.pipeline.create_change_request("Bad Change", "bob")
        job_id = self.pipeline.add_job(req_id, self.device_id)
        
        self.pipeline.run_plan_stage(job_id, "cisco", "b", {})
        
        # Execute with check that fails
        checks = [VerificationCheck(name="Ping", check_type="connectivity", target="1.1.1.1")]
        success = self.pipeline.execute_job(job_id, "h", "u", "p", checks=checks)
        
        self.assertFalse(success)
        print("[PASS] Job correctly reported failure")
        
        # Verify Driver Calls: Push (Apply) -> Run Command (Verify Fail) -> Push (Rollback)
        self.assertEqual(mock_driver.push_config.call_count, 2) 
        # Check arguments of 2nd push call (Rollback)
        args, _ = mock_driver.push_config.call_args
        self.assertIn("undo bad config", args[0][0])
        print("[PASS] Rollback triggered with correct commands")
        
        with self.db._connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            self.assertEqual(job["status"], JobStatus.ROLLED_BACK)
            print("[PASS] DB status is ROLLED_BACK")

if __name__ == "__main__":
    unittest.main()
