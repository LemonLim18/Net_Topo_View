"""
M4 Deployment Pipeline — The 6-Stage Orchestrator.

Manages the lifecycle of a configuration change:
1. PLAN: Generate config & rollback (M3)
2. APPROVE: (Manual step, handled by API/Status check)
3. SNAPSHOT_PRE: Backup current config (M1)
4. APPLY: Push config (M1)
5. VERIFY: Run checks (verifier.py) -> Rollback on failure
6. COMMIT: Mark success + Snapshot Post (M1)
"""

import logging
import json
import traceback
from datetime import datetime
from typing import List, Optional, Dict

from backend.db.manager import DatabaseManager
from backend.db.models import ConfigSnapshot
from backend.drivers.factory import DriverFactory
from backend.drivers.base import BaseDriver
from backend.llm.network_tools import generate_config, generate_rollback
from backend.deploy.models import (
    Job, JobStatus, PipelineStage, ChangeRequest, RequestStatus, 
    DeploymentRepository, JobStep
)
from backend.deploy.verifier import Verifier, VerificationCheck, VerificationResult

logger = logging.getLogger("deploy.pipeline")

class DeploymentPipeline:
    """
    Core orchestrator for network changes.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.repo = DeploymentRepository(db_manager)

    def create_change_request(
        self, title: str, requester: str, description: str = ""
    ) -> int:
        """Step 0: Create the Request container."""
        req = ChangeRequest(
            title=title, description=description, requester=requester
        )
        return self.repo.create_request(req)

    def add_job(self, request_id: int, device_id: int) -> int:
        """Add a device job to a request."""
        job = Job(request_id=request_id, device_id=device_id)
        return self.repo.create_job(job)

    def run_plan_stage(self, job_id: int, vendor: str, feature: str, params: Dict) -> bool:
        """
        Stage 1: PLAN.
        Generate config and rollback commands using M3 templates.
        """
        self.repo.log_step(job_id, PipelineStage.PLAN, f"Generating config for {feature}")
        try:
            # Generate Config
            cfg_res = json.loads(generate_config(vendor, feature, json.dumps(params)))
            if "error" in cfg_res:
                raise ValueError(cfg_res["error"])
            
            # Generate Rollback
            rb_res = json.loads(generate_rollback(vendor, feature, json.dumps(params)))
            if "error" in rb_res:
                logger.warning(f"Rollback generation failed: {rb_res['error']}")
                rollback_cmd = None
            else:
                rollback_cmd = rb_res.get("rollback_config")

            # Update Job
            with self.db_manager._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET generated_config = ?, rollback_config = ?, stage = ?, status = ? WHERE id = ?",
                    (cfg_res["config"], rollback_cmd, PipelineStage.PLAN.value, JobStatus.PENDING.value, job_id)
                )
            
            self.repo.log_step(job_id, PipelineStage.PLAN, "Plan generated successfully")
            return True

        except Exception as e:
            msg = f"Plan failed: {str(e)}"
            logger.error(msg)
            self.repo.update_job_status(job_id, JobStatus.FAILED, PipelineStage.PLAN, msg)
            self.repo.log_step(job_id, PipelineStage.PLAN, msg)
            return False

    def execute_job(
        self, 
        job_id: int, 
        host: str, 
        username: str, 
        password: str, 
        driver_type: str = "cisco_ios",
        checks: List[VerificationCheck] = []
    ) -> bool:
        """
        Execute Stages 3-6 (Snapshot -> Apply -> Verify -> Commit).
        Assumes Stage 2 (Approval) is already granted.
        """
        
        # 0. Setup Driver
        driver = None
        try:
            # Load Job
            # (We query DB manually here or add a get_job to repo)
            with self.db_manager._connect() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    raise ValueError(f"Job {job_id} not found")
                
                job = Job(
                    id=row["id"], request_id=row["request_id"], device_id=row["device_id"],
                    status=row["status"], stage=row["stage"],
                    generated_config=row["generated_config"],
                    rollback_config=row["rollback_config"]
                )

            if not job.generated_config:
                raise ValueError("No generated config found. Run PLAN stage first.")

            # Connect
            self.repo.update_job_status(job_id, JobStatus.RUNNING, PipelineStage.SNAPSHOT_PRE)
            driver = DriverFactory.create(
                driver_type, host=host, username=username, password=password
            )
            driver.connect()

            # -----------------------------------------------------------
            # Stage 3: SNAPSHOT PRE
            # -----------------------------------------------------------
            self.repo.log_step(job_id, PipelineStage.SNAPSHOT_PRE, "Capturing pre-change state")
            pre_config = driver.get_running_config()
            # Save snapshot
            snap_id = self.db_manager.save_config_snapshot(
                ConfigSnapshot(device_id=job.device_id, config_text=pre_config)
            )
            
            with self.db_manager._connect() as conn:
                conn.execute("UPDATE jobs SET snapshot_pre_id = ? WHERE id = ?", (snap_id, job_id))

            # -----------------------------------------------------------
            # Stage 4: APPLY
            # -----------------------------------------------------------
            self.repo.update_job_status(job_id, JobStatus.RUNNING, PipelineStage.APPLY)
            self.repo.log_step(job_id, PipelineStage.APPLY, "Pushing configuration")
            
            commands = job.generated_config.strip().splitlines()
            result = driver.push_config(commands)
            
            self.repo.log_step(
                job_id, PipelineStage.APPLY, 
                f"Result: Success={result.success}\nOutput:\n{result.output}"
            )

            if not result.success:
                raise RuntimeError(f"Config push failed: {result.error_message}")

            # -----------------------------------------------------------
            # Stage 5: VERIFY
            # -----------------------------------------------------------
            self.repo.update_job_status(job_id, JobStatus.RUNNING, PipelineStage.VERIFY)
            verifier = Verifier(driver)
            
            # 1. Run checks
            # 2. If any fail -> Rollback
            
            check_results = verifier.run_checks(checks)
            failed_checks = [r for r in check_results if not r.pass_]
            
            # Log results
            log_msg = "\n".join([f"[{'PASS' if r.pass_ else 'FAIL'}] {r.check.name}: {r.details}" for r in check_results])
            self.repo.log_step(job_id, PipelineStage.VERIFY, log_msg)

            if failed_checks:
                logger.warning(f"Verification failed ({len(failed_checks)} checks). Rolling back.")
                raise RuntimeError("Verification failed")

            # -----------------------------------------------------------
            # Stage 6: COMMIT
            # -----------------------------------------------------------
            self.repo.update_job_status(job_id, JobStatus.RUNNING, PipelineStage.COMMIT)
            self.repo.log_step(job_id, PipelineStage.COMMIT, "Change verified. Saving post-snapshot.")
            
            post_config = driver.get_running_config()
            snap_post_id = self.db_manager.save_config_snapshot(
                ConfigSnapshot(device_id=job.device_id, config_text=post_config)
            )
            
            with self.db_manager._connect() as conn:
                conn.execute("UPDATE jobs SET snapshot_post_id = ? WHERE id = ?", (snap_post_id, job_id))

            self.repo.update_job_status(job_id, JobStatus.SUCCESS, PipelineStage.COMMIT)
            return True

        except Exception as e:
            # -----------------------------------------------------------
            # ROLLBACK (On Failure)
            # -----------------------------------------------------------
            err_msg = str(e)
            logger.error(f"Job {job_id} failed: {err_msg}")
            
            self.repo.update_job_status(job_id, JobStatus.RUNNING, PipelineStage.ROLLBACK, err_msg)
            self.repo.log_step(job_id, PipelineStage.ROLLBACK, f"Triggering rollback due to: {err_msg}")
            
            rollback_success = False
            if driver and driver.is_alive() and job.rollback_config:
                try:
                    rb_cmds = job.rollback_config.strip().splitlines()
                    rb_res = driver.push_config(rb_cmds)
                    self.repo.log_step(job_id, PipelineStage.ROLLBACK, f"Rollback output:\n{rb_res.output}")
                    if rb_res.success:
                        rollback_success = True
                except Exception as rb_err:
                    self.repo.log_step(job_id, PipelineStage.ROLLBACK, f"Rollback CRITICAL ERROR: {str(rb_err)}")
            else:
                self.repo.log_step(job_id, PipelineStage.ROLLBACK, "Skipping rollback: No driver or no rollback config.")

            status = JobStatus.ROLLED_BACK if rollback_success else JobStatus.FAILED
            self.repo.update_job_status(job_id, status, PipelineStage.ROLLBACK, err_msg)
            return False

        finally:
            if driver:
                driver.disconnect()

    def get_job_logs(self, job_id: int) -> List[Dict]:
        """Fetch logs for a job."""
        with self.db_manager._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_steps WHERE job_id = ? ORDER BY id ASC", (job_id,)
            ).fetchall()
            return [dict(r) for r in rows]
