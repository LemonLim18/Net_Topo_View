"""
M4 Deployment Pipeline — The 6-Stage Orchestrator.

Manages the lifecycle of a configuration change:
1. PLAN: Generate config & rollback (M3)
2. APPROVE: (Manual step, handled by API/Status check)
3. SNAPSHOT_PRE: Backup current config (M1)
4. APPLY: Push config (M1)
5. VERIFY: Run checks (verifier.py) -> recommend manual rollback on failure
6. COMMIT: Mark success + Snapshot Post (M1)
"""

import logging
import json
import traceback
from datetime import datetime
from typing import List, Optional, Dict

from config_engine.rollback_planner import build_rollback_plan
from db.manager import DatabaseManager
from db.models import ConfigSnapshot
from drivers.factory import DriverFactory
from drivers.base import BaseDriver
from llm.network_tools import generate_config, generate_rollback
from deploy.models import (
    Job, JobStatus, PipelineStage, ChangeRequest, RequestStatus,
    DeploymentRepository, JobStep, ExecutionEngine
)
from deploy.verifier import Verifier, VerificationCheck, VerificationResult

# M22 — lazy imports to avoid circular import issues at module load time
_ansible_available = False
try:
    from ansible.router import route_execution, ExecutionAction   # noqa: F401
    from ansible.runner import run_playbook                        # noqa: F401
    from ansible.inventory import build_inventory                  # noqa: F401
    _ansible_available = True
except ImportError:
    pass

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

    def run_plan_stage(
        self,
        job_id: int,
        vendor: str,
        feature: str,
        params: Dict,
        generated_config: Optional[str] = None,
        rollback_config: Optional[str] = None,
        execution_engine: Optional[str] = None,
    ) -> bool:
        """
        Stage 1: PLAN.
        Generate config and rollback commands using M3 templates, or persist a
        prevalidated artifact supplied by the proposal approval path.
        """
        self.repo.log_step(job_id, PipelineStage.PLAN, f"Generating config for {feature}")
        try:
            with self.db_manager._connect() as conn:
                row = conn.execute("SELECT device_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise ValueError(f"Job {job_id} not found")
            device = self.db_manager.get_device_by_id(row["device_id"])
            hostname = device.hostname if device else ""

            if generated_config is None:
                cfg_res = json.loads(generate_config(vendor, feature, json.dumps(params)))
                if "error" in cfg_res:
                    raise ValueError(cfg_res["error"])
                planned_config = cfg_res["config"]
            else:
                planned_config = generated_config

            template_rollback = rollback_config
            if template_rollback is None:
                rb_res = json.loads(generate_rollback(vendor, feature, json.dumps(params)))
                if "error" in rb_res:
                    logger.warning(f"Rollback generation failed: {rb_res['error']}")
                    template_rollback = None
                else:
                    template_rollback = rb_res.get("rollback_config")

            rollback_plan = build_rollback_plan(
                db_manager=self.db_manager,
                hostname=hostname,
                vendor=vendor,
                feature=feature,
                params=params,
                generated_config=planned_config,
                template_rollback_config=template_rollback or "",
            )
            rollback_cmd = template_rollback or None
            engine_value = execution_engine or ExecutionEngine.SSH_FALLBACK.value

            # Update Job
            with self.db_manager._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET generated_config = ?, rollback_config = ?, execution_engine = ?, stage = ?, status = ? WHERE id = ?",
                    (planned_config, rollback_cmd, engine_value, PipelineStage.PLAN.value, JobStatus.PENDING.value, job_id)
                )
            
            self.repo.log_step(job_id, PipelineStage.PLAN, f"Plan generated successfully. Rollback: {rollback_plan['summary']}")
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
        current_stage = PipelineStage.SNAPSHOT_PRE
        
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
                    snapshot_pre_id=row["snapshot_pre_id"],
                    snapshot_post_id=row["snapshot_post_id"],
                    generated_config=row["generated_config"],
                    rollback_config=row["rollback_config"],
                    # M22 — may be NULL in older rows → default to ssh_fallback
                    execution_engine=ExecutionEngine(row["execution_engine"]) if row["execution_engine"] else ExecutionEngine.SSH_FALLBACK,
                    ansible_playbook=row["ansible_playbook"] if row["ansible_playbook"] else None,
                )

            if not job.generated_config:
                raise ValueError("No generated config found. Run PLAN stage first.")

            # Connect
            self.repo.update_job_status(job_id, JobStatus.RUNNING, PipelineStage.SNAPSHOT_PRE)
            current_stage = PipelineStage.SNAPSHOT_PRE
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
                ConfigSnapshot(
                    device_id=job.device_id,
                    config_text=pre_config,
                    snapshot_type="rollback",      # pre-change snapshot reserved for rollback
                    source_type="deploy_pipeline",
                    execution_id=job_id,
                )
            )
            
            with self.db_manager._connect() as conn:
                conn.execute("UPDATE jobs SET snapshot_pre_id = ? WHERE id = ?", (snap_id, job_id))
            job.snapshot_pre_id = snap_id

            # -----------------------------------------------------------
            # Stage 4: APPLY  (M22 — Ansible-first with SSH fallback)
            # -----------------------------------------------------------
            self.repo.update_job_status(job_id, JobStatus.RUNNING, PipelineStage.APPLY)
            current_stage = PipelineStage.APPLY
            self.repo.log_step(job_id, PipelineStage.APPLY, "Pushing configuration")

            engine = job.execution_engine or ExecutionEngine.SSH_FALLBACK

            if _ansible_available and engine in (
                ExecutionEngine.ANSIBLE_NATIVE, ExecutionEngine.ANSIBLE_CLI
            ) and job.ansible_playbook:
                # ── Ansible path ──────────────────────────────────────────
                inventory = build_inventory(target_hosts=[host])
                ans_result = run_playbook(
                    playbook_content=job.ansible_playbook,
                    inventory=inventory,
                    extra_vars={},
                    check_mode=False,
                    job_id=str(job_id),
                )
                log_tail = ans_result.stdout[-3000:] if ans_result.stdout else ""
                self.repo.log_step(
                    job_id, PipelineStage.APPLY,
                    f"Ansible [{engine.value}] rc={ans_result.rc} "
                    f"changed={ans_result.changed}\n{log_tail}"
                )
                if not ans_result.success:
                    raise RuntimeError(
                        f"Ansible run failed (rc={ans_result.rc}): {ans_result.stderr[:500]}"
                    )

            elif engine == ExecutionEngine.NO_OP:
                # ── RAG confirmed already converged — skip apply ──────────
                self.repo.log_step(
                    job_id, PipelineStage.APPLY,
                    "NO_OP: RAG state already matches intent — skipping push"
                )

            else:
                # ── SSH fallback  (original Paramiko/Netmiko path) ────────
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
            current_stage = PipelineStage.VERIFY
            verifier = Verifier(driver)
            
            # 1. Run checks
            # 2. If any fail -> require explicit human-approved rollback
            
            check_results = verifier.run_checks(checks)
            failed_checks = [r for r in check_results if not r.pass_]
            
            # Log results
            log_msg = "\n".join([f"[{'PASS' if r.pass_ else 'FAIL'}] {r.check.name}: {r.details}" for r in check_results])
            self.repo.log_step(job_id, PipelineStage.VERIFY, log_msg)

            if failed_checks:
                logger.warning(f"Verification failed ({len(failed_checks)} checks). Manual rollback review is required.")
                raise RuntimeError("Verification failed")

            # -----------------------------------------------------------
            # Stage 6: COMMIT
            # -----------------------------------------------------------
            self.repo.update_job_status(job_id, JobStatus.RUNNING, PipelineStage.COMMIT)
            self.repo.log_step(job_id, PipelineStage.COMMIT, "Change verified. Saving post-snapshot.")
            
            post_config = driver.get_running_config()
            # Baseline promotion gate:
            # Only promote to baseline if the request/job was approved (approval gate),
            # otherwise store as 'actual' and keep compliance baseline unchanged.
            approved_by = None
            request_status = None
            job_status = None
            try:
                with self.db_manager._connect() as conn:
                    jr = conn.execute(
                        "SELECT request_id, status FROM jobs WHERE id = ?",
                        (job_id,),
                    ).fetchone()
                    if jr:
                        job_status = jr["status"]
                        rid = jr["request_id"]
                        rr = conn.execute(
                            "SELECT status FROM change_requests WHERE id = ?",
                            (rid,),
                        ).fetchone()
                        request_status = rr["status"] if rr else None
                        # Try read the approver from deploy.audit_log (if present)
                        try:
                            ar = conn.execute(
                                "SELECT actor, created_at FROM audit_log "
                                "WHERE request_id = ? AND action = 'approve' "
                                "ORDER BY id DESC LIMIT 1",
                                (rid,),
                            ).fetchone()
                            if ar and ar.get("actor"):
                                approved_by = ar["actor"]
                        except Exception:
                            approved_by = None
            except Exception:
                approved_by = None

            is_approved = False
            try:
                if str(request_status or "").strip().lower() == "approved":
                    is_approved = True
                if str(job_status or "").strip().lower() == "approved":
                    is_approved = True
            except Exception:
                is_approved = False

            snap_post_id = self.db_manager.save_config_snapshot(
                ConfigSnapshot(
                    device_id=job.device_id,
                    config_text=post_config,
                    snapshot_type="baseline" if is_approved else "actual",
                    source_type="deploy_pipeline",
                    execution_id=job_id,
                    approved_by=approved_by if is_approved else None,
                    verified_by="pipeline",
                    verified_at=datetime.utcnow().isoformat(),
                )
            )
            
            with self.db_manager._connect() as conn:
                conn.execute("UPDATE jobs SET snapshot_post_id = ? WHERE id = ?", (snap_post_id, job_id))

            self.repo.update_job_status(job_id, JobStatus.SUCCESS, PipelineStage.COMMIT)
            return True

        except Exception as e:
            err_msg = str(e)
            logger.error(f"Job {job_id} failed: {err_msg}")

            if driver:
                try:
                    driver.disconnect()
                except Exception:
                    pass
                driver = None

            self.repo.update_job_status(job_id, JobStatus.FAILED, current_stage, err_msg)
            self.repo.log_step(
                job_id,
                PipelineStage.ROLLBACK,
                "Automatic rollback is disabled in Milestone 3. Review the rollback plan, post-change verification report, and approve rollback explicitly if needed.",
            )
            return False

        finally:
            if driver:
                driver.disconnect()

    def get_all_jobs(self) -> List[Dict]:
        """List all jobs with their status."""
        # Join with requests to get request title? For now just jobs.
        return self.repo.list_jobs()

    def get_job_logs(self, job_id: int) -> List[Dict]:
        """Fetch logs for a job."""
        with self.db_manager._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_steps WHERE job_id = ? ORDER BY id ASC", (job_id,)
            ).fetchall()
            return [dict(r) for r in rows]
