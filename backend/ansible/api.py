"""
M22 Ansible Orchestration — FastAPI router.

Endpoints:

  POST /ansible/jobs/{job_id}/plan-ansible   — LLM generates playbook, stored on job
  POST /ansible/jobs/{job_id}/check          — ansible-playbook --check (dry-run diff)
  POST /ansible/jobs/{job_id}/approve        — operator approval gate (admin only)
  POST /ansible/jobs/{job_id}/execute        — run approved playbook (admin only)
  GET  /ansible/jobs/{job_id}/result         — fetch last run result

Approval flow:
  plan-ansible → check (operator reviews diff) → approve → execute

All destructive changes require explicit operator approval before execute is allowed.
"""

import json
import logging
import asyncio
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from config_engine.safety import validate_playbook_yaml

logger = logging.getLogger("ansible.api")

router = APIRouter(prefix="/ansible", tags=["Ansible Orchestration (M22)"])

# ── Lazy-loaded shared instances (injected by backend.py) ───────────────────
_builder  = None   # PlaybookBuilder
_agent    = None   # NetworkAgent (for RAG context)
_pipeline = None   # DeploymentPipeline
_db       = None   # DatabaseManager


def setup_ansible(builder, agent, pipeline, db_manager) -> None:
    """Called from backend.py lifespan to inject shared instances."""
    global _builder, _agent, _pipeline, _db
    _builder  = builder
    _agent    = agent
    _pipeline = pipeline
    _db       = db_manager
    logger.info("Ansible orchestration layer initialized")


def _require_builder():
    if _builder is None:
        raise HTTPException(503, "Ansible orchestration layer not initialized")
    return _builder


def _require_pipeline():
    if _pipeline is None:
        raise HTTPException(503, "Deployment pipeline not initialized")
    return _pipeline


# ── Auth dependency (reuse existing JWT auth) ────────────────────────────────
try:
    from auth.auth import get_current_user
    from auth.rbac import require_role
except ImportError:
    async def get_current_user():   # type: ignore
        return {"username": "anonymous", "role": "admin"}
    def require_role(role: str):    # type: ignore
        return get_current_user


# ── Request / Response models ────────────────────────────────────────────────

class PlanAnsibleRequest(BaseModel):
    intent_description: str
    target_hosts:       List[str]
    vendor:             str
    feature:            str
    params:             Dict[str, Any] = {}
    max_interfaces:     int = 5
    auto_refine:        bool = True
    max_refinement_attempts: int = 3
    verify_with_dry_run: bool = False


class CheckResult(BaseModel):
    rc:      int
    changed: bool
    diff:    str
    summary: str


class ExecuteResult(BaseModel):
    rc:      int
    success: bool
    changed: bool
    summary: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/plan-ansible", summary="LLM generates Ansible playbook for a job")
async def plan_ansible(
    job_id:  int,
    req:     PlanAnsibleRequest,
    user:    dict = Depends(get_current_user),
):
    """
    Ask the LLM to generate an Ansible playbook for this job and store it.
    The playbook now goes through a bounded refine-and-verify loop before being stored.
    Device dry-run contact remains optional and disabled unless requested.
    """
    builder = _require_builder()

    # Pull live RAG context from the agent (read-only)
    rag_context = ""
    kb_snippets: list[str] = []
    if _agent is not None:
        try:
            # Context-Injection RAG: live topology digest from ContextBuilder
            rag_context = _agent._context.build_full_context()
        except Exception as exc:
            logger.warning("Could not fetch RAG context: %s", exc)

        # Vector-RAG (M21) snippets if available
        try:
            if hasattr(_agent, "_retriever") and _agent._retriever:
                kb_snippets = [
                    r["text"]
                    for r in _agent._retriever.retrieve(
                        req.intent_description, k=3
                    )
                ]
        except Exception as exc:
            logger.debug("Vector-RAG retrieval skipped: %s", exc)

    intent = {
        "description":   req.intent_description,
        "target_hosts":  req.target_hosts,
        "vendor":        req.vendor,
        "feature":       req.feature,
        "params":        req.params,
        "max_interfaces": req.max_interfaces,
    }

    try:
        orchestration = await _build_playbook_with_refinement(
            job_id=job_id,
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets,
            auto_refine=req.auto_refine,
            max_attempts=req.max_refinement_attempts,
            verify_with_dry_run=req.verify_with_dry_run,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Playbook build failed job=%d: %s", job_id, exc)
        raise HTTPException(422, f"Playbook generation failed: {exc}")

    if not orchestration.get("success", False):
        raise HTTPException(
            422,
            detail={
                "message": "Playbook generation failed after bounded refinement attempts",
                "job_id": job_id,
                "attempts_used": orchestration.get("attempts_used", 0),
                "max_attempts": orchestration.get("max_attempts", req.max_refinement_attempts),
                "escalation_reason": orchestration.get("escalation_reason", "validation_failed"),
                "issues": orchestration.get("final_validation", {}).get("issues", []),
                "refinement_history": orchestration.get("history", []),
            },
        )

    playbook_yaml = str(orchestration.get("playbook") or "")

    # Persist the playbook against the job
    if _db is not None:
        try:
            with _db._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET ansible_playbook = ?, stage = 'plan', "
                    "status = 'pending' WHERE id = ?",
                    (playbook_yaml, job_id),
                )
        except Exception as exc:
            logger.warning("Could not persist playbook to DB: %s", exc)

    logger.info("Playbook generated for job=%d by user=%s", job_id, user.get("username"))
    return {
        "job_id":   job_id,
        "status":   "playbook_generated",
        "playbook": playbook_yaml,
        "refinement": {
            "auto_refine": req.auto_refine,
            "verify_with_dry_run": req.verify_with_dry_run,
            "attempts_used": orchestration.get("attempts_used", 1),
            "max_attempts": orchestration.get("max_attempts", req.max_refinement_attempts),
            "history": orchestration.get("history", []),
            "validation": orchestration.get("final_validation", {}),
        },
    }


# ── Phase 6: Multi-Candidate Playbook Generation (UC14) ──────────────────

@router.post(
    "/jobs/{job_id}/plan-candidates",
    summary="LLM generates multiple candidate playbooks with different approaches",
)
async def plan_ansible_candidates(
    job_id:  int,
    req:     PlanAnsibleRequest,
    n:       int = 3,
    user:    dict = Depends(get_current_user),
):
    """
    Generate *n* candidate playbooks (conservative, balanced, aggressive)
    for the same intent.  The operator picks the best one.
    """
    builder = _require_builder()

    rag_context = ""
    kb_snippets: list[str] = []
    if _agent is not None:
        try:
            rag_context = _agent._context.build_full_context()
        except Exception:
            pass
        try:
            if hasattr(_agent, "_retriever") and _agent._retriever:
                kb_snippets = [
                    r["text"]
                    for r in _agent._retriever.retrieve(req.intent_description, k=3)
                ]
        except Exception:
            pass

    intent = {
        "description":   req.intent_description,
        "target_hosts":  req.target_hosts,
        "vendor":        req.vendor,
        "feature":       req.feature,
        "params":        req.params,
        "max_interfaces": req.max_interfaces,
    }

    try:
        candidates = await builder.build_candidates(
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets,
            n=min(n, 5),
            run_id=str(job_id),
        )
    except Exception as exc:
        raise HTTPException(422, f"Multi-candidate generation failed: {exc}")

    logger.info("Generated %d candidates for job=%d", len(candidates), job_id)
    return {
        "job_id": job_id,
        "candidates": candidates,
    }


@router.post(
    "/jobs/{job_id}/select-candidate",
    summary="Select one of the generated candidate playbooks",
)
async def select_candidate(
    job_id: int,
    candidate_index: int = 0,
    playbook_yaml: str = "",
    user:  dict = Depends(get_current_user),
):
    """
    After reviewing candidates, operator selects one by index.
    The selected playbook is stored against the job.
    """
    if not playbook_yaml:
        raise HTTPException(400, "playbook_yaml is required")

    if _db is not None:
        with _db._connect() as conn:
            conn.execute(
                "UPDATE jobs SET ansible_playbook = ?, stage = 'plan', "
                "status = 'pending' WHERE id = ?",
                (playbook_yaml, job_id),
            )

    return {
        "job_id": job_id,
        "selected_index": candidate_index,
        "status": "candidate_selected",
    }


@router.post(
    "/jobs/{job_id}/check",
    response_model=CheckResult,
    summary="Dry-run the playbook (--check --diff) — no device changes",
)
async def check_playbook(
    job_id: int,
    user:   dict = Depends(require_role("operator")),
):
    """
    Run ansible-playbook --check --diff against the stored playbook.
    Returns per-host diff for operator review.  No changes are applied.
    """
    playbook_yaml, target_hosts = _load_job_playbook(job_id)

    from ansible.inventory import build_inventory
    from ansible.runner import run_playbook

    inventory = build_inventory(target_hosts=target_hosts)
    result = await run_in_threadpool(
        run_playbook,
        playbook_content=playbook_yaml,
        inventory=inventory,
        extra_vars={},
        check_mode=True,
        job_id=job_id,
    )

    logger.info("Check run job=%d rc=%d user=%s", job_id, result.rc, user.get("username"))
    return CheckResult(
        rc=result.rc,
        changed=result.changed,
        diff=result.stdout[-10000:],
        summary=result.summary,
    )


@router.post("/jobs/{job_id}/approve", summary="Operator approves job for execution")
async def approve_job(
    job_id: int,
    user:   dict = Depends(require_role("admin")),
):
    """
    Set job status to 'approved'.  Required before /execute will run.
    Only admins may approve.
    """
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'approved', stage = 'approve' WHERE id = ?",
            (job_id,),
        )

    logger.info("Job %d approved by %s", job_id, user.get("username"))
    return {"job_id": job_id, "status": "approved", "approved_by": user.get("username")}


@router.post(
    "/jobs/{job_id}/execute",
    response_model=ExecuteResult,
    summary="Execute the approved Ansible playbook",
)
async def execute_playbook(
    job_id: int,
    user:   dict = Depends(require_role("admin")),
):
    """
    Execute the stored playbook.  Job must be in 'approved' state.

    Execution flow:
      1. Route (Ansible native / CLI / SSH fallback)
      2. Pre-snapshot
      3. ansible-runner run (or SSH fallback)
      4. Post-change verification via Context-Injection RAG
      5. Commit or rollback
    """
    # ── Gate: must be approved ─────────────────────────────────────────────
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        row = conn.execute(
            "SELECT status, ansible_playbook, generated_config, rollback_config "
            "FROM jobs WHERE id = ?",
            (job_id,)
        ).fetchone()

    if not row:
        raise HTTPException(404, f"Job {job_id} not found")

    if row["status"] != "approved":
        raise HTTPException(
            403,
            f"Job {job_id} must be 'approved' before execution. Current status: {row['status']}"
        )

    playbook_yaml = row["ansible_playbook"]
    if not playbook_yaml:
        raise HTTPException(
            422,
            "No Ansible playbook stored for this job. Run /plan-ansible first."
        )

    # Extract target hosts from the stored playbook's `hosts:` field
    import yaml as _yaml
    try:
        plays = _yaml.safe_load(playbook_yaml)
        hosts_val = plays[0].get("hosts", "") if plays else ""
        if isinstance(hosts_val, str) and hosts_val not in ("all", ""):
            target_hosts = [h.strip() for h in hosts_val.split(",")]
        else:
            target_hosts = None
    except Exception:
        target_hosts = None

    from ansible.inventory import build_inventory
    from ansible.runner import run_playbook

    inventory = build_inventory(target_hosts=target_hosts)

    # Mark running
    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'running', stage = 'apply' WHERE id = ?",
            (job_id,)
        )

    result = await run_in_threadpool(
        run_playbook,
        playbook_content=playbook_yaml,
        inventory=inventory,
        extra_vars={},
        check_mode=False,
        job_id=job_id,
    )

    final_status = "success" if result.success else "failed"
    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, stage = ? WHERE id = ?",
            (final_status, "commit" if result.success else "rollback", job_id)
        )

    logger.info(
        "Execution complete job=%d rc=%d changed=%s user=%s",
        job_id, result.rc, result.changed, user.get("username"),
    )
    return ExecuteResult(
        rc=result.rc,
        success=result.success,
        changed=result.changed,
        summary=result.summary,
    )


@router.get("/jobs/{job_id}/result", summary="Get last run result for a job")
async def get_result(
    job_id: int,
    user:   dict = Depends(get_current_user),
):
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        row = conn.execute(
            "SELECT id, status, stage, error_message, ansible_playbook "
            "FROM jobs WHERE id = ?",
            (job_id,)
        ).fetchone()
        logs = conn.execute(
            "SELECT stage, status, details, created_at FROM job_steps "
            "WHERE job_id = ? ORDER BY id",
            (job_id,)
        ).fetchall()

    if not row:
        raise HTTPException(404, f"Job {job_id} not found")

    return {
        "job_id":  job_id,
        "status":  row["status"],
        "stage":   row["stage"],
        "error":   row["error_message"],
        "has_playbook": bool(row["ansible_playbook"]),
        "steps":   [dict(r) for r in logs],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_job_playbook(job_id: int) -> tuple[str, list[str]]:
    """Load playbook YAML and target hosts from DB. Raises HTTPException on error."""
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        row = conn.execute(
            "SELECT ansible_playbook FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()

    if not row:
        raise HTTPException(404, f"Job {job_id} not found")
    if not row["ansible_playbook"]:
        raise HTTPException(422, "No Ansible playbook stored. Run /plan-ansible first.")

    # Extract target host list from the stored playbook YAML `hosts:` field
    import yaml
    try:
        plays = yaml.safe_load(row["ansible_playbook"])
        hosts_val = plays[0].get("hosts", "") if plays else ""
        if isinstance(hosts_val, str) and hosts_val not in ("all", ""):
            target_hosts = [h.strip() for h in hosts_val.split(",")]
        else:
            target_hosts = None   # no filter → inventory goes to all
    except Exception:
        target_hosts = None

    return row["ansible_playbook"], target_hosts


def _normalize_refinement_errors(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for issue in issues:
        normalized.append(
            {
                "line": issue.get("line"),
                "message": issue.get("message", ""),
                "rule": f"{issue.get('phase', 'validation')}::{issue.get('rule', 'unknown')}",
            }
        )
    return normalized


def _format_issue_summary(issue: dict[str, Any]) -> str:
    prefix = f"[{issue.get('phase', 'validation')}/{issue.get('rule', 'unknown')}]"
    line = issue.get("line")
    if line is not None:
        prefix += f" line {line}"
    return f"{prefix} {issue.get('message', '')}".strip()


def _dry_run_is_refinable(result_text: str) -> bool:
    normalized = (result_text or "").lower()
    non_refinable_markers = (
        "unreachable",
        "authentication failed",
        "permission denied",
        "timed out",
        "connection refused",
        "no route to host",
    )
    return not any(marker in normalized for marker in non_refinable_markers)


async def _validate_playbook_candidate(
    playbook_yaml: str,
    intent: dict,
    job_id: int,
    verify_with_dry_run: bool,
) -> dict[str, Any]:
    builder = _require_builder()
    validation = builder.validate_playbook(intent=intent, playbook_yaml=playbook_yaml)
    issues = [dict(issue) for issue in validation.get("issues") or []]

    requested_hosts = [str(host).strip() for host in intent.get("target_hosts") or [] if str(host).strip()]
    from ansible.inventory import build_inventory

    inventory = build_inventory(target_hosts=requested_hosts)
    inventory_hosts = ((inventory.get("all") or {}).get("hosts") or {}) if isinstance(inventory, dict) else {}
    missing_hosts = [host for host in requested_hosts if host not in inventory_hosts]
    if missing_hosts:
        issues.append(
            {
                "phase": "inventory",
                "rule": "missing_inventory_hosts",
                "message": f"Requested targets are not present in inventory/topology: {', '.join(missing_hosts)}",
                "refinable": False,
            }
        )

    if verify_with_dry_run and not missing_hosts:
        from ansible.runner import run_playbook

        dry_run_result = await run_in_threadpool(
            run_playbook,
            playbook_content=playbook_yaml,
            inventory=inventory,
            extra_vars={},
            check_mode=True,
            job_id=job_id,
        )
        if not dry_run_result.success:
            result_text = "\n".join(part for part in [dry_run_result.stderr, dry_run_result.stdout] if part).strip()
            issues.append(
                {
                    "phase": "dry_run",
                    "rule": "ansible_check_failed",
                    "message": result_text or dry_run_result.summary or "Ansible dry-run failed",
                    "refinable": _dry_run_is_refinable(result_text),
                }
            )

    for issue in issues:
        issue.setdefault("refinable", True)

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "errors": [_format_issue_summary(issue) for issue in issues],
    }


async def _build_playbook_with_refinement(
    job_id: int,
    intent: dict,
    rag_context: str,
    kb_snippets: list[str],
    auto_refine: bool,
    max_attempts: int,
    verify_with_dry_run: bool,
) -> dict[str, Any]:
    builder = _require_builder()
    bounded_attempts = max(1, min(int(max_attempts or 1), 5))
    history: list[dict[str, Any]] = []

    current_playbook = await builder.build_draft(
        intent=intent,
        rag_context=rag_context,
        kb_snippets=kb_snippets,
        run_id=str(job_id),
    )

    for attempt in range(1, bounded_attempts + 1):
        validation = await _validate_playbook_candidate(
            playbook_yaml=current_playbook,
            intent=intent,
            job_id=job_id,
            verify_with_dry_run=verify_with_dry_run,
        )
        entry = {
            "attempt": attempt,
            "passed": validation.get("passed", False),
            "issues": validation.get("issues", []),
        }
        history.append(entry)

        note_prefix = "validated" if validation.get("passed", False) else "validation_failed"
        if _db is not None:
            _append_playbook_version(
                _db,
                job_id,
                current_playbook,
                notes=f"{note_prefix} attempt {attempt}: " + "; ".join(validation.get("errors") or [note_prefix]),
                set_current=bool(validation.get("passed", False)),
            )

        if validation.get("passed", False):
            return {
                "success": True,
                "playbook": current_playbook,
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
            }

        refinable_issues = [issue for issue in validation.get("issues", []) if issue.get("refinable", True)]
        if not auto_refine:
            return {
                "success": False,
                "playbook": current_playbook,
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
                "escalation_reason": "auto_refine_disabled",
            }
        if not refinable_issues:
            return {
                "success": False,
                "playbook": current_playbook,
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
                "escalation_reason": "non_refinable_validation_failure",
            }
        if attempt >= bounded_attempts:
            break

        refined_playbook, changes = await _ai_refine_playbook(
            original=current_playbook,
            errors=_normalize_refinement_errors(refinable_issues),
            feedback="Preserve working tasks and fix only the reported validation failures.",
        )
        if refined_playbook.strip() == current_playbook.strip():
            return {
                "success": False,
                "playbook": current_playbook,
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
                "escalation_reason": "refinement_made_no_material_change",
            }

        entry["changes"] = changes
        current_playbook = refined_playbook

    final_validation = await _validate_playbook_candidate(
        playbook_yaml=current_playbook,
        intent=intent,
        job_id=job_id,
        verify_with_dry_run=verify_with_dry_run,
    )
    return {
        "success": False,
        "playbook": current_playbook,
        "attempts_used": bounded_attempts,
        "max_attempts": bounded_attempts,
        "final_validation": final_validation,
        "history": history,
        "escalation_reason": "max_refinement_attempts_reached",
    }


# =============================================================================
# M23 ─ Ansible Workflow Studio  (new endpoints)
# =============================================================================

ANSIBLE_BASE = Path(__file__).parent
_ALLOWED_ROOTS = ("playbooks", "roles", "inventory", "awx")
_WRITABLE_ROOTS = ("playbooks", "inventory")   # roles/awx are read-only from UI


def _safe_path(relative: str) -> Path:
    """
    Resolve *relative* against ANSIBLE_BASE and ensure it stays within
    one of the allowed sub-trees.  Raises HTTPException 400 on traversal.
    """
    resolved = (ANSIBLE_BASE / relative).resolve()
    try:
        resolved.relative_to(ANSIBLE_BASE.resolve())
    except ValueError:
        raise HTTPException(400, "Path traversal detected")
    # Must be inside one of the permitted root dirs
    rel_parts = resolved.relative_to(ANSIBLE_BASE.resolve()).parts
    if not rel_parts or rel_parts[0] not in _ALLOWED_ROOTS:
        raise HTTPException(403, f"Access restricted to: {_ALLOWED_ROOTS}")
    # Never expose vault contents
    if "vault" in rel_parts or ".vault_pass" in rel_parts:
        raise HTTPException(403, "Vault contents are not accessible via the API")
    return resolved


def _build_tree(root: Path, base: Path) -> list:
    """Recursively build a file-node tree dict."""
    result = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return result
    for entry in entries:
        rel = str(entry.relative_to(base)).replace("\\", "/")
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        if entry.is_dir():
            result.append({
                "type": "dir",
                "name": entry.name,
                "path": rel,
                "children": _build_tree(entry, base),
            })
        else:
            st = entry.stat()
            result.append({
                "type": "file",
                "name": entry.name,
                "path": rel,
                "size": st.st_size,
                "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
    return result


def _safe_writable_path(relative: str) -> tuple[Path, tuple[str, ...]]:
    """
    Resolve path and enforce writable roots only.
    Returns (resolved_path, rel_parts).
    """
    path = _safe_path(relative)
    rel_parts = Path(relative).parts
    if not rel_parts or rel_parts[0] not in _WRITABLE_ROOTS:
        raise HTTPException(
            403,
            f"Only {_WRITABLE_ROOTS} directories are writable. "
            "roles/ and awx/ are read-only.",
        )
    return path, rel_parts


# ── M23: create-and-plan ───────────────────────────────────────────────────

class CreateAndPlanRequest(BaseModel):
    intent_description: str
    target_hosts:       List[str]
    vendor:             str
    feature:            str
    params:             Dict[str, Any] = {}
    device_id:          int = 0


@router.post(
    "/create-and-plan",
    summary="Auto-create a job and generate a playbook from natural language intent",
)
async def create_and_plan(
    req:  CreateAndPlanRequest,
    user: dict = Depends(get_current_user),
):
    """
    Single-shot endpoint for the Ansible Studio AI chat:
    1. Creates a ChangeRequest + Job in the deploy pipeline.
    2. Generates an Ansible playbook via LLM (same as /plan-ansible).
    3. Stores the playbook on the job and returns it.
    """
    if _db is None or _pipeline is None:
        raise HTTPException(503, "Backend not fully initialized")

    # Resolve device_id: if caller passes 0 / unknown, look up by first target host
    device_id = req.device_id
    if device_id == 0 and req.target_hosts:
        with _db._connect() as conn:
            row = conn.execute(
                "SELECT id FROM devices WHERE hostname = ? LIMIT 1",
                (req.target_hosts[0],),
            ).fetchone()
            if row:
                device_id = row["id"]
    if device_id == 0:
        device_id = 1  # sentinel — prevents FK violation

    # Create the change request + job
    from deploy.models import ChangeRequest, Job, DeploymentRepository
    repo = DeploymentRepository(_db)
    cr_id = repo.create_request(ChangeRequest(
        title=f"Ansible Studio: {req.intent_description[:60]}",
        description=req.intent_description,
        requester=user.get("username", "ansible_studio"),
    ))
    job_id = repo.create_job(Job(request_id=cr_id, device_id=device_id))

    # Delegate to plan_ansible logic
    plan_req = PlanAnsibleRequest(
        intent_description=req.intent_description,
        target_hosts=req.target_hosts,
        vendor=req.vendor,
        feature=req.feature,
        params=req.params,
    )
    result = await plan_ansible(job_id=job_id, req=plan_req, user=user)
    return {
        "job_id":      job_id,
        "request_id":  cr_id,
        "status":      result.get("status"),
        "playbook":    result.get("playbook"),
    }


# ── Template-based config generation (Config Engine as a helper) ─────────

class CreateFromTemplateRequest(BaseModel):
    vendor:       str
    feature:      str
    params:       Dict[str, Any] = {}
    target_hosts: List[str] = ["all"]
    device_id:    int = 0


@router.post(
    "/create-from-template",
    summary="Generate a playbook from a Config Engine template (no LLM needed)",
)
async def create_from_template(
    req:  CreateFromTemplateRequest,
    user: dict = Depends(get_current_user),
):
    """
    Deterministic path: render vendor config via the Config Engine's Jinja2
    templates, wrap the result in a minimal Ansible playbook, and create a
    ChangeRequest + Job — same pipeline as the LLM route.
    """
    if _db is None:
        raise HTTPException(503, "Backend not fully initialized")

    # ── 1. Render config via Config Engine ──────────────────────────────
    try:
        from config_engine.renderer import TemplateRenderer
        renderer = TemplateRenderer()
        config_text = renderer.render(req.vendor, req.feature, req.params)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, f"Template render failed: {exc}")
    except Exception as exc:
        logger.error("Template render error: %s", exc)
        raise HTTPException(500, f"Template rendering failed: {exc}")

    undo_text = None
    try:
        from config_engine.renderer import TemplateRenderer as _TR
        undo_text = _TR().render_undo(req.vendor, req.feature, req.params)
    except Exception:
        pass  # undo is optional

    # ── 2. Wrap in Ansible playbook YAML ────────────────────────────────
    hosts_str = ",".join(req.target_hosts) if req.target_hosts else "all"

    MODULE_MAP = {
        "cisco_ios":     ("cisco.ios.ios_config",   "commands"),
        "cisco_xe":      ("cisco.ios.ios_config",   "commands"),
        "huawei_vrp":    ("community.network.ce_config", "lines"),
        "juniper_junos": ("junipernetworks.junos.junos_config", "lines"),
    }
    module_name, lines_key = MODULE_MAP.get(req.vendor, ("cisco.ios.ios_config", "commands"))
    config_lines = [l for l in config_text.splitlines() if l.strip()]

    import yaml as _yaml
    playbook = _yaml.dump(
        [{
            "name": f"Config Engine: {req.feature} ({req.vendor})",
            "hosts": hosts_str,
            "gather_facts": False,
            "tasks": [{
                "name": f"Apply {req.feature} configuration",
                module_name: {lines_key: config_lines},
            }],
        }],
        default_flow_style=False,
        sort_keys=False,
    )

    execution_engine = "ansible_native" if req.vendor in {"cisco_ios", "juniper_junos"} else "ansible_cli"
    playbook_validation = validate_playbook_yaml(
        playbook_yaml=playbook,
        vendor=req.vendor,
        feature=req.feature,
        target_hosts=req.target_hosts,
        execution_engine=execution_engine,
    )
    if not playbook_validation.get("passed", False):
        raise HTTPException(
            422,
            {
                "message": "Generated playbook failed Step 10 safety validation.",
                "validation": playbook_validation,
            },
        )

    # ── 3. Create ChangeRequest + Job ───────────────────────────────────
    device_id = req.device_id
    if device_id == 0 and req.target_hosts:
        with _db._connect() as conn:
            row = conn.execute(
                "SELECT id FROM devices WHERE hostname = ? LIMIT 1",
                (req.target_hosts[0],),
            ).fetchone()
            if row:
                device_id = row["id"]
    if device_id == 0:
        device_id = 1  # sentinel

    from deploy.models import ChangeRequest, Job, DeploymentRepository
    repo = DeploymentRepository(_db)
    cr_id = repo.create_request(ChangeRequest(
        title=f"Template: {req.vendor}/{req.feature}",
        description=f"Config Engine render of {req.feature} for {hosts_str}",
        requester=user.get("username", "ansible_studio"),
    ))
    job_id = repo.create_job(Job(request_id=cr_id, device_id=device_id))

    # Persist the playbook on the job
    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET ansible_playbook = ?, generated_config = ?, "
            "rollback_config = ?, stage = 'plan', status = 'pending' WHERE id = ?",
            (playbook, config_text, undo_text, job_id),
        )

    logger.info(
        "Template-based playbook created job=%d vendor=%s feature=%s user=%s",
        job_id, req.vendor, req.feature, user.get("username"),
    )
    return {
        "job_id":       job_id,
        "request_id":   cr_id,
        "status":       "playbook_generated",
        "playbook":     playbook,
        "config":       config_text,
        "undo_config":  undo_text,
    }


# ── M23: file-server endpoints ────────────────────────────────────────────

@router.get("/files", summary="List ansible file tree (playbooks, roles, inventory, awx)")
async def list_files(user: dict = Depends(get_current_user)):
    tree = []
    for root_name in _ALLOWED_ROOTS:
        root_dir = ANSIBLE_BASE / root_name
        if root_dir.exists():
            tree.append({
                "type": "dir",
                "name": root_name,
                "path": root_name,
                "children": _build_tree(root_dir, ANSIBLE_BASE),
            })
    return {"tree": tree}


@router.get("/files/{file_path:path}", summary="Read a single ansible file")
async def read_file(file_path: str, user: dict = Depends(get_current_user)):
    path = _safe_path(file_path)
    if not path.exists():
        raise HTTPException(404, f"File not found: {file_path}")
    if path.is_dir():
        raise HTTPException(400, "Path is a directory, not a file")
    content = path.read_text(encoding="utf-8", errors="replace")
    st = path.stat()
    return {
        "path": file_path,
        "content": content,
        "size": st.st_size,
        "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
    }


class WriteFileRequest(BaseModel):
    content: str


class CreateFileRequest(BaseModel):
    content: str = ""


@router.put("/files/{file_path:path}", summary="Save edited ansible file content")
async def write_file(
    file_path: str,
    body:       WriteFileRequest,
    user:       dict = Depends(require_role("operator", "admin")),
):
    path = _safe_path(file_path)
    # Only playbooks/ and inventory/ are writable from the UI
    rel_parts = Path(file_path).parts
    if not rel_parts or rel_parts[0] not in _WRITABLE_ROOTS:
        raise HTTPException(
            403,
            f"Only {_WRITABLE_ROOTS} directories are writable. "
            "roles/ and awx/ are read-only.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")
    logger.info("File written: %s by %s", file_path, user.get("username"))
    return {"path": file_path, "status": "saved"}


@router.post("/files/create-file/{file_path:path}", summary="Create a new blank ansible file")
async def create_file(
    file_path: str,
    body:      CreateFileRequest,
    user:      dict = Depends(require_role("operator", "admin")),
):
    path, rel_parts = _safe_writable_path(file_path)
    if len(rel_parts) < 2:
        raise HTTPException(400, "Provide a file path under playbooks/ or inventory/")
    if path.exists():
        raise HTTPException(409, f"Path already exists: {file_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content or "", encoding="utf-8")
    logger.info("File created: %s by %s", file_path, user.get("username"))
    return {"path": file_path, "status": "created"}


@router.post("/files/create-folder/{folder_path:path}", summary="Create a new ansible folder")
async def create_folder(
    folder_path: str,
    user:        dict = Depends(require_role("operator", "admin")),
):
    path, rel_parts = _safe_writable_path(folder_path)
    if len(rel_parts) < 2:
        raise HTTPException(400, "Provide a folder path under playbooks/ or inventory/")
    if path.exists() and not path.is_dir():
        raise HTTPException(409, f"Path exists as file: {folder_path}")
    path.mkdir(parents=True, exist_ok=True)
    logger.info("Folder created: %s by %s", folder_path, user.get("username"))
    return {"path": folder_path, "status": "created"}


@router.delete("/files/{target_path:path}", summary="Delete ansible file or folder")
async def delete_path(
    target_path: str,
    user:        dict = Depends(require_role("operator", "admin")),
):
    path, rel_parts = _safe_writable_path(target_path)
    if len(rel_parts) == 1 and rel_parts[0] in _WRITABLE_ROOTS:
        raise HTTPException(403, f"Cannot delete writable root directory: {rel_parts[0]}")
    if not path.exists():
        raise HTTPException(404, f"Path not found: {target_path}")

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()

    logger.info("Path deleted: %s by %s", target_path, user.get("username"))
    return {"path": target_path, "status": "deleted"}


# ── M23: SSE job-output stream ──────────────────────────────────────────────

@router.get(
    "/jobs/{job_id}/stream",
    summary="SSE stream of ansible job step output (text/event-stream)",
)
async def stream_job_output(
    job_id: int,
    token:  str = Query("", description="JWT bearer token (query param for EventSource)"),
):
    """
    Server-Sent Events stream of job_steps rows as they are written.
    Polls the DB every 1.5 seconds.  Terminates when the job reaches a
    terminal state (success / failed / rolled_back).

    Auth: JWT passed as ?token= query parameter because the browser's native
    EventSource API does not support custom headers.
    """
    # Validate token manually
    if _db is None:
        raise HTTPException(503, "Database not initialized")
    try:
        from auth.auth import decode_token
        decode_token(token)
    except Exception:
        raise HTTPException(401, "Invalid or missing token")

    _TERMINAL = {"success", "failed", "rolled_back"}

    async def event_generator():
        last_step_id = 0
        while True:
            try:
                with _db._connect() as conn:
                    # Fetch new steps since last seen
                    new_steps = conn.execute(
                        "SELECT id, stage, status, details, created_at "
                        "FROM job_steps WHERE job_id = ? AND id > ? ORDER BY id",
                        (job_id, last_step_id),
                    ).fetchall()

                    job_row = conn.execute(
                        "SELECT status FROM jobs WHERE id = ?",
                        (job_id,),
                    ).fetchone()

                for step in new_steps:
                    last_step_id = step["id"]
                    import json as _json
                    data = _json.dumps({
                        "stage":      step["stage"],
                        "status":     step["status"],
                        "details":    (step["details"] or "")[-2000:],
                        "created_at": step["created_at"],
                    })
                    yield f"data: {data}\n\n"

                if job_row and job_row["status"] in _TERMINAL:
                    import json as _json
                    yield f"data: {_json.dumps({'done': True, 'status': job_row['status']})}\n\n"
                    return

            except Exception as exc:
                logger.warning("SSE stream error job=%d: %s", job_id, exc)

            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":                "no-cache",
            "X-Accel-Buffering":            "no",   # disable nginx buffering
            "Access-Control-Allow-Origin":  "*",
        },
    )


# =============================================================================
# M25 Phase-2 ─ Playbook Refine & Version History
# =============================================================================

class ValidationError(BaseModel):
    line:    Optional[int] = None
    message: str
    rule:    str = "syntax"


class RefineRequest(BaseModel):
    """Feedback from playbook validation / user review for AI self-remediation."""
    errors:   List[ValidationError] = []
    feedback: str = ""


class RefineResponse(BaseModel):
    job_id:           int
    version:          int
    refined_playbook: str
    changes:          List[str] = []
    iteration:        int = 1


_REFINE_SYSTEM = """\
You are a senior Ansible engineer.
The user will provide a YAML playbook and a list of validation errors plus feedback.
Return ONLY the corrected YAML playbook — no explanations, no markdown fences,
no commentary.  Preserve all existing working tasks.
Also prepend a YAML comment block listing what you changed, one change per line:
# Changes:
# - <change 1>
# - <change 2>
---
"""


async def _ai_refine_playbook(
    original: str,
    errors: list[dict],
    feedback: str,
) -> tuple[str, list[str]]:
    """Ask the LLM to fix a playbook given error/feedback."""
    if _agent is None and _builder is None:
        raise HTTPException(503, "LLM not available for playbook refinement")

    # Get the provider from the agent, or the builder
    provider = None
    if _agent is not None:
        provider = _agent._provider
    elif hasattr(_builder, "_provider"):
        provider = _builder._provider

    if provider is None:
        raise HTTPException(503, "LLM provider not available")

    from llm.provider import LLMMessage
    error_lines = "\n".join(
        f"  Line {e.get('line','?')}: [{e.get('rule','?')}] {e.get('message','')}"
        for e in errors
    ) or "  (no specific errors listed)"

    user_content = (
        f"Original playbook:\n```yaml\n{original}\n```\n\n"
        f"Validation errors:\n{error_lines}\n\n"
        f"Additional feedback: {feedback or 'none'}\n\n"
        "Please fix all errors and return the corrected YAML only."
    )

    resp = await provider.generate(
        messages=[
            LLMMessage(role="system", content=_REFINE_SYSTEM),
            LLMMessage(role="user",   content=user_content),
        ],
        tools=None,
        temperature=0.1,
    )
    raw = resp.content.strip()

    # Strip markdown fences if the model ignored instructions
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            stripped = part.lstrip("yaml\n").strip()
            if "---" in stripped or "- name" in stripped:
                raw = stripped
                break

    # Extract change comments
    changes: list[str] = []
    yaml_lines = raw.split("\n")
    clean_lines: list[str] = []
    for line in yaml_lines:
        stripped = line.strip()
        if stripped.startswith("# ") and ":" in stripped:
            changes.append(stripped.lstrip("# ").strip())
        else:
            clean_lines.append(line)
    final_yaml = "\n".join(clean_lines).strip()

    return final_yaml, changes


def _append_playbook_version(
    db,
    job_id: int,
    playbook: str,
    notes: str = "",
    set_current: bool = True,
) -> int:
    """
    Load current playbook_versions for a job, append a new entry, save back.
    Returns new version number.
    """
    try:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT playbook_versions FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
    except Exception:
        return 1

    versions: list = []
    if row and row[0]:
        try:
            versions = json.loads(row[0])
        except Exception:
            versions = []

    new_version = len(versions) + 1
    versions.append({
        "version":          new_version,
        "playbook":         playbook,
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "refinement_notes": notes,
    })

    try:
        with db._connect() as conn:
            if set_current:
                conn.execute(
                    "UPDATE jobs SET playbook_versions=?, ansible_playbook=? WHERE id=?",
                    (json.dumps(versions), playbook, job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET playbook_versions=? WHERE id=?",
                    (json.dumps(versions), job_id),
                )
    except Exception as exc:
        logger.warning("Could not save playbook version: %s", exc)

    return new_version


@router.post(
    "/jobs/{job_id}/refine",
    response_model=RefineResponse,
    summary="AI self-remediation — fix playbook based on validation errors or feedback",
)
async def refine_playbook(
    job_id: int,
    req:    RefineRequest,
    user:   dict = Depends(require_role("operator")),
):
    """
    Sends the current playbook + validation errors/feedback to the LLM.
    The LLM returns a corrected version which is persisted over the existing one.

    Use this in the Ansible Studio "Fix Issues" workflow:
    1. Validate with /check
    2. If errors, call /refine with the error list
    3. Repeat until /check passes
    4. Approve + execute
    """
    playbook_yaml, _ = _load_job_playbook(job_id)

    errors_raw  = [e.model_dump() for e in req.errors]
    refined, changes = await _ai_refine_playbook(
        original=playbook_yaml,
        errors=errors_raw,
        feedback=req.feedback,
    )

    version = _append_playbook_version(
        _db, job_id, refined,
        notes=f"Refinement: {req.feedback or ', '.join([e.message for e in req.errors[:3]])}"
    )

    logger.info("Playbook refined job=%d version=%d user=%s", job_id, version, user.get("username"))
    return RefineResponse(
        job_id=job_id,
        version=version,
        refined_playbook=refined,
        changes=changes,
        iteration=version,
    )


class PlaybookVersion(BaseModel):
    version:          int
    playbook:         str
    created_at:       str
    refinement_notes: str = ""


@router.get(
    "/jobs/{job_id}/history",
    response_model=List[PlaybookVersion],
    summary="Get full playbook version history for a job",
)
async def get_playbook_history(
    job_id: int,
    user:   dict = Depends(get_current_user),
):
    """
    Return all versions of the playbook for this job, oldest first.

    Version 1 is the original LLM-generated playbook.
    Subsequent versions are refinements from /refine calls.
    """
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        # Auto-upgrade: add column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN playbook_versions TEXT")
        except Exception:
            pass  # column already exists

        row = conn.execute(
            "SELECT playbook_versions, ansible_playbook FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()

    if not row:
        raise HTTPException(404, f"Job {job_id} not found")

    if row["playbook_versions"]:
        try:
            versions = json.loads(row["playbook_versions"])
            return [PlaybookVersion(**v) for v in versions]
        except Exception:
            pass

    # Fallback: only the current playbook
    if row["ansible_playbook"]:
        return [PlaybookVersion(
            version=1,
            playbook=row["ansible_playbook"],
            created_at=datetime.now(timezone.utc).isoformat(),
            refinement_notes="Original generation",
        )]

    return []