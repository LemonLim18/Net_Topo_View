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

from ansible.bundle_artifacts import AnsibleArtifactBundle, bundle_from_dict
from ansible.planning_context import (
    build_scoped_planning_context,
    infer_vendor_hint,
    normalize_vendor,
    planning_context_readiness,
    planning_context_to_prompt_text,
    reconcile_inventory_state,
    topology_override_from_planning_context,
)
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


def _resolve_target_hosts(target_hosts: List[str]) -> List[str]:
    """
    Expand semantic group names (all, access, switches, core, distribution) to
    actual device hostnames from the device DB. Fixes intent like "all access
    switches" being parsed as literal ["all", "access", "switches"].
    """
    if not target_hosts or _db is None:
        return list(target_hosts) if target_hosts else []

    devices = _db.get_all_devices()
    all_hostnames = [d.hostname for d in devices if d.hostname]
    role_lower = {d.hostname: (d.role or "").lower() for d in devices if d.hostname}
    name_lower = {d.hostname: (d.hostname or "").lower() for d in devices if d.hostname}

    def is_access(h: str) -> bool:
        r = role_lower.get(h, "")
        n = name_lower.get(h, "")
        return r == "access" or n.startswith("acc_")

    def is_core(h: str) -> bool:
        r = role_lower.get(h, "")
        n = name_lower.get(h, "")
        return r == "core" or n.startswith("core_")

    def is_distribution(h: str) -> bool:
        r = role_lower.get(h, "")
        n = name_lower.get(h, "")
        return r == "distribution" or n.startswith("dist_")

    tokens_lower = [str(x).strip().lower() for x in target_hosts if str(x).strip()]
    if "all" in tokens_lower and ("access" in tokens_lower or "switches" in tokens_lower):
        return [h for h in all_hostnames if is_access(h)]
    if "all" in tokens_lower and ("core" in tokens_lower or "distribution" in tokens_lower or "dist" in tokens_lower):
        return [h for h in all_hostnames if is_core(h) or is_distribution(h)]

    expanded: List[str] = []
    seen: set = set()
    for raw in target_hosts:
        t = str(raw).strip().lower()
        if not t:
            continue
        if t == "all":
            for h in all_hostnames:
                if h not in seen:
                    seen.add(h)
                    expanded.append(h)
            continue
        if t in ("access", "switches", "access_switches"):
            for h in all_hostnames:
                if is_access(h) and h not in seen:
                    seen.add(h)
                    expanded.append(h)
            continue
        if t == "core":
            for h in all_hostnames:
                if is_core(h) and h not in seen:
                    seen.add(h)
                    expanded.append(h)
            continue
        if t in ("distribution", "dist"):
            for h in all_hostnames:
                if is_distribution(h) and h not in seen:
                    seen.add(h)
                    expanded.append(h)
            continue
        # Concrete hostname: keep if it exists in DB
        for d in devices:
            if d.hostname and d.hostname.lower() == t:
                if d.hostname not in seen:
                    seen.add(d.hostname)
                    expanded.append(d.hostname)
                break
        else:
            # Not a known group and not found in DB; keep original for clearer error
            orig = str(raw).strip()
            if orig not in seen:
                seen.add(orig)
                expanded.append(orig)
    return expanded if expanded else list(target_hosts)


def _build_topo_from_db(hostnames: List[str]) -> dict:
    """Build a minimal topology dict from the device DB for inventory when topology file is empty."""
    if not hostnames or _db is None:
        return {"nodes": {}}
    nodes = {}
    for d in _db.get_all_devices():
        if not d.hostname or (hostnames and d.hostname not in hostnames):
            continue
        vendor = (d.vendor or "").lower().replace("-", "_").replace(" ", "_")
        nodes[d.hostname] = {
            "id": d.hostname,
            "hostname": d.hostname,
            "name": d.hostname,
            "mgmt_ip": d.management_ip or d.hostname,
            "ip": d.management_ip or d.hostname,
            "vendor": vendor,
        }
    return {"nodes": nodes}


def _provider_for_request(provider: Optional[str], model: Optional[str], ollama_base_url: Optional[str]):
    if provider and str(provider).strip():
        from llm.api import _instantiate_runtime_provider

        return _instantiate_runtime_provider(str(provider).strip(), model, ollama_base_url)
    if _agent is not None and hasattr(_agent, "_provider"):
        return getattr(_agent, "_provider")
    if _builder is not None and hasattr(_builder, "_provider"):
        return getattr(_builder, "_provider")
    return None


def _build_intent_parse_context(intent_description: str) -> str:
    if _db is None:
        return ""
    try:
        from rag.context_bundle import ContextBundleBuilder

        bundle = ContextBundleBuilder(db_manager=_db).build_bundle(
            include_inventory=True,
            include_planned_topology=True,
            include_telemetry=False,
            include_sdn=False,
            include_config=False,
            include_deployment=False,
            include_incidents=False,
            include_threats=False,
            include_drift=False,
            include_knowledge=False,
            include_freshness_warnings=True,
            query=intent_description,
        )
        return bundle.to_prompt_text()
    except Exception as exc:
        logger.warning("Could not build intent parse context: %s", exc)
        return ""


def _policy_validate_intent(intent_json: str, user_role: str) -> dict[str, Any]:
    from llm.policy import PolicyEngine

    engine = PolicyEngine(db_manager=_db)
    raw = engine.validate(intent_json, user_role=user_role)
    return json.loads(raw)


def _resolve_backend_intent_request(req, user: dict) -> tuple["PlanAnsibleRequest", dict[str, Any], dict[str, Any]]:
    from llm.intent import IntentParser, IntentType, ParsedIntent

    parser = IntentParser(
        provider=_provider_for_request(req.provider, req.model, req.ollama_base_url),
        db_manager=_db,
    )
    parsed = ParsedIntent.model_validate_json(
        parser.parse(
            req.intent_description,
            topology_context=_build_intent_parse_context(req.intent_description),
        )
    )

    if req.feature:
        parsed.feature = str(req.feature).strip()
    if req.vendor:
        parsed.vendor = normalize_vendor(req.vendor)
    if req.params:
        parsed.parameters = dict(req.params)
    if req.target_hosts:
        parsed.scope.device_hostnames = list(req.target_hosts)
        parsed.scope.all_devices = False
        parsed.resolved_devices = _resolve_target_hosts(req.target_hosts)
    else:
        parsed.resolved_devices = _resolve_target_hosts(parsed.resolved_devices)

    if not parsed.vendor:
        parsed.vendor = infer_vendor_hint(parsed.resolved_devices, _db)

    if parsed.requires_disambiguation:
        raise HTTPException(422, parsed.disambiguation_message or "Intent requires disambiguation before planning.")
    if parsed.intent_type not in {IntentType.CONFIGURE, IntentType.DEPLOY}:
        raise HTTPException(422, f"Intent '{parsed.intent_type.value}' is not a configuration change.")
    if not parsed.feature:
        raise HTTPException(422, "Intent parser did not resolve a configuration feature.")
    if not parsed.resolved_devices:
        raise HTTPException(422, "Intent parser did not resolve any target devices.")

    policy = _policy_validate_intent(
        parsed.model_dump_json(),
        user_role=str(user.get("role") or "operator"),
    )
    if policy.get("approved") is not True:
        raise HTTPException(
            422,
            detail={
                "message": "Parsed intent was rejected by policy validation.",
                "intent": parsed.model_dump(),
                "policy": policy,
            },
        )

    plan_req = PlanAnsibleRequest(
        intent_description=req.intent_description,
        target_hosts=parsed.resolved_devices,
        vendor=normalize_vendor(parsed.vendor or infer_vendor_hint(parsed.resolved_devices, _db)),
        feature=parsed.feature,
        params=dict(parsed.parameters or {}),
        max_interfaces=getattr(req, "max_interfaces", 5),
        auto_refine=getattr(req, "auto_refine", True),
        max_refinement_attempts=getattr(req, "max_refinement_attempts", 3),
        verify_with_dry_run=getattr(req, "verify_with_dry_run", False),
        provider=req.provider,
        model=req.model,
        ollama_base_url=req.ollama_base_url,
    )
    return plan_req, parsed.model_dump(), policy


async def _ensure_planning_context(
    *,
    target_hosts: List[str],
    vendor: str,
    intent_description: str,
    feature: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if _db is None or not hasattr(_db, "get_all_devices"):
        return _minimal_planning_context(
            target_hosts,
            vendor=vendor,
            intent_description=intent_description,
            feature=feature,
        ), {"reconciled": False, "issues": ["database_inventory_unavailable"]}

    planning_context = build_scoped_planning_context(
        _db,
        target_hosts,
        vendor_filter=vendor or None,
        query=intent_description,
        feature=feature,
    )
    readiness = planning_context_readiness(planning_context)
    if readiness.get("ready"):
        return planning_context, {"reconciled": False, "issues": []}

    reconciliation = await run_in_threadpool(reconcile_inventory_state, _db)
    planning_context = build_scoped_planning_context(
        _db,
        target_hosts,
        vendor_filter=vendor or None,
        query=intent_description,
        feature=feature,
    )
    post_reconcile = planning_context_readiness(planning_context)
    if not post_reconcile.get("ready"):
        raise HTTPException(
            422,
            detail={
                "message": "Inventory reconciliation completed but planning context is still incomplete.",
                "issues": post_reconcile.get("issues", []),
                "reconciliation": reconciliation,
            },
        )
    return planning_context, {
        "reconciled": True,
        "issues": readiness.get("issues", []),
        "reconciliation": reconciliation,
    }


def _build_inventory_for_hosts(
    target_hosts: List[str],
    planning_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from ansible.inventory import build_inventory

    topology_override = None
    if planning_context:
        topology_override = topology_override_from_planning_context(planning_context)
    if topology_override is None or not (topology_override.get("nodes") or {}):
        topology_override = _build_topo_from_db(target_hosts)
    inventory = build_inventory(target_hosts=target_hosts or None, topology_override=topology_override)
    _inject_device_credentials_into_inventory(inventory)
    return inventory


def _inject_device_credentials_into_inventory(inventory: dict[str, Any]) -> None:
    """
    Override ansible_user and ansible_password for each host with values from the
    local devices table (ssh_user, ssh_password) when set. This makes Ansible
    check/execute use device credentials from the NetBox controller registration.
    """
    if _db is None:
        return
    all_group = inventory.get("all") or {}
    hosts = all_group.get("hosts") or {}
    if not isinstance(hosts, dict):
        return
    for hostname, host_vars in hosts.items():
        if not isinstance(host_vars, dict):
            continue
        try:
            dev = _db.get_device_by_hostname(hostname)
            if dev and (dev.ssh_user or dev.ssh_password):
                if dev.ssh_user:
                    host_vars["ansible_user"] = (dev.ssh_user or "").strip()
                if dev.ssh_password:
                    host_vars["ansible_password"] = (dev.ssh_password or "").strip()
        except Exception:
            pass


def _minimal_planning_context(
    target_hosts: List[str],
    *,
    vendor: str,
    intent_description: str,
    feature: str,
) -> dict[str, Any]:
    return {
        "target_hosts": list(target_hosts or []),
        "feature": str(feature or ""),
        "query": str(intent_description or ""),
        "inventory": {
            "source": "request",
            "source_detail": "Using request-scoped fallback planning context.",
            "freshness": {
                "source": "request",
                "scope": "planned_inventory",
                "freshness_class": "live",
                "confidence": 0.5,
            },
            "total_devices": len(target_hosts or []),
            "interfaces_included": 0,
        },
        "planned_topology": {
            "source": "request",
            "source_detail": "No planned topology context available in fallback mode.",
            "freshness": {
                "source": "request",
                "scope": "planned_topology",
                "freshness_class": "live",
                "confidence": 0.5,
            },
            "link_count": 0,
        },
        "freshness_warnings": [],
        "fallback_evidence": [{"domain": "planned_inventory", "source": "request", "detail": "fallback"}],
        "devices": [
            {
                "hostname": host,
                "management_ip": host,
                "vendor": normalize_vendor(vendor),
                "role": "",
                "site": "",
                "model": None,
                "status": None,
                "serial": None,
                "interfaces": [],
                "neighbors": [],
                "inventory_source": "request",
                "planned_topology_source": "request",
                "live_neighbor_source": None,
            }
            for host in (target_hosts or [])
        ],
    }


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
    target_hosts:       List[str] = []
    vendor:             str = ""
    feature:            str = ""
    params:             Dict[str, Any] = {}
    max_interfaces:     int = 5
    auto_refine:        bool = True
    max_refinement_attempts: int = 3
    verify_with_dry_run: bool = False
    provider:           Optional[str] = None  # "gemini" | "ollama" for playbook LLM
    model:              Optional[str] = None
    ollama_base_url:    Optional[str] = None


class CheckResult(BaseModel):
    rc:      int
    changed: bool
    diff:    str
    summary: str
    stderr:  str = ""  # When rc != 0, often contains connection/auth/task errors


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
    resolved_hosts = _resolve_target_hosts(req.target_hosts)
    effective_vendor = normalize_vendor(req.vendor or infer_vendor_hint(resolved_hosts, _db))
    planning_context, planning_meta = await _ensure_planning_context(
        target_hosts=resolved_hosts,
        vendor=effective_vendor,
        intent_description=req.intent_description,
        feature=req.feature,
    )

    # Pull live RAG context from the agent (read-only)
    rag_context = ""
    kb_snippets: list[str] = []
    if _agent is not None:
        try:
            # Context-Injection RAG: live topology digest from ContextBuilder
            rag_context = _agent._context.build_full_context(
                device_hostnames=resolved_hosts,
                query=req.intent_description,
            )
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
        "target_hosts":  resolved_hosts,
        "vendor":        effective_vendor,
        "feature":       req.feature,
        "params":        req.params,
        "max_interfaces": req.max_interfaces,
        "planning_context": planning_context,
        "device_inventory_context": planning_context_to_prompt_text(planning_context),
    }

    use_bundle = bool(getattr(builder, "supports_role_bundle", lambda *_: False)(req.feature))
    try:
        if use_bundle:
            orchestration = await _build_bundle_with_refinement(
                job_id=job_id,
                intent=intent,
                rag_context=rag_context,
                kb_snippets=kb_snippets,
                auto_refine=req.auto_refine,
                max_attempts=req.max_refinement_attempts,
                verify_with_dry_run=req.verify_with_dry_run,
                provider=req.provider,
                model=req.model,
                ollama_base_url=req.ollama_base_url,
            )
        else:
            orchestration = await _build_playbook_with_refinement(
                job_id=job_id,
                intent=intent,
                rag_context=rag_context,
                kb_snippets=kb_snippets,
                auto_refine=req.auto_refine,
                max_attempts=req.max_refinement_attempts,
                verify_with_dry_run=req.verify_with_dry_run,
                provider=req.provider,
                model=req.model,
                ollama_base_url=req.ollama_base_url,
            )
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc).strip() or repr(exc)
        logger.exception(
            "Playbook build failed job=%d type=%s detail=%s",
            job_id,
            type(exc).__name__,
            detail,
        )
        raise HTTPException(422, f"Playbook generation failed: {detail}")

    if not orchestration.get("success", False):
        detail = {
            "message": "Playbook generation failed after bounded refinement attempts",
            "job_id": job_id,
            "attempts_used": orchestration.get("attempts_used", 0),
            "max_attempts": orchestration.get("max_attempts", req.max_refinement_attempts),
            "escalation_reason": orchestration.get("escalation_reason", "validation_failed"),
            "issues": orchestration.get("final_validation", {}).get("issues", []),
            "refinement_history": orchestration.get("history", []),
        }
        logger.error("Playbook generation failed job=%d: %s", job_id, detail)
        raise HTTPException(
            422,
            detail=detail,
        )

    playbook_yaml = str(orchestration.get("playbook") or "")
    bundle_payload = orchestration.get("bundle")

    if _db is not None:
        try:
            if bundle_payload:
                bundle_manifest = dict(bundle_payload)
                bundle_manifest.setdefault("manifest", {})
                bundle_manifest["manifest"].update(
                    {
                        "job_id": job_id,
                        "target_hosts": resolved_hosts,
                        "provider": req.provider or "gemini",
                        "model": req.model or "",
                        "artifact_type": "bundle",
                        "inventory_reconciled": bool(planning_meta.get("reconciled")),
                    }
                )
                _persist_job_bundle(job_id=job_id, bundle=bundle_manifest, playbook_yaml=playbook_yaml)
                _append_playbook_version(
                    _db,
                    job_id,
                    playbook_yaml,
                    notes="Original bundle generation",
                    bundle=bundle_manifest,
                )
                bundle_payload = bundle_manifest
            else:
                with _db._connect() as conn:
                    conn.execute(
                        "UPDATE jobs SET ansible_playbook = ?, stage = 'plan', "
                        "status = 'pending' WHERE id = ?",
                        (playbook_yaml, job_id),
                    )
                _append_playbook_version(
                    _db,
                    job_id,
                    playbook_yaml,
                    notes="Original playbook generation",
                )
        except Exception as exc:
            logger.warning("Could not persist playbook to DB: %s", exc)

    logger.info("Playbook generated for job=%d by user=%s", job_id, user.get("username"))
    return {
        "job_id":   job_id,
        "status":   "playbook_generated",
        "playbook": playbook_yaml,
        "artifact_type": "bundle" if bundle_payload else "playbook",
        "entrypoint_path": (bundle_payload or {}).get("entrypoint_path"),
        "files": (bundle_payload or {}).get("files", []),
        "manifest": {
            **((bundle_payload or {}).get("manifest", {})),
            "inventory_reconciled": bool(planning_meta.get("reconciled")),
        },
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

    resolved_hosts = _resolve_target_hosts(req.target_hosts)
    intent = {
        "description":   req.intent_description,
        "target_hosts":  resolved_hosts,
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
    user:   dict = Depends(get_current_user),
):
    """
    Run ansible-playbook --check --diff against the stored playbook.
    Returns per-host diff for review.  No changes are applied; any authenticated user may run check.
    """
    artifact = _load_job_artifact(job_id)
    from ansible.runner import run_playbook

    try:
        planning_context = (
            build_scoped_planning_context(
                _db,
                artifact["target_hosts"] or [],
                query=artifact["playbook_yaml"],
            )
            if _db is not None and hasattr(_db, "get_all_devices")
            else _minimal_planning_context(
                artifact["target_hosts"] or [],
                vendor="",
                intent_description=artifact["playbook_yaml"],
                feature="",
            )
        )
    except Exception:
        planning_context = _minimal_planning_context(
            artifact["target_hosts"] or [],
            vendor="",
            intent_description=artifact["playbook_yaml"],
            feature="",
        )
    inventory = _build_inventory_for_hosts(artifact["target_hosts"], planning_context)
    result = await run_in_threadpool(
        run_playbook,
        inventory=inventory,
        extra_vars={},
        check_mode=True,
        job_id=job_id,
        playbook_content=artifact["playbook_yaml"],
        playbook_path=artifact["entrypoint_path"] or None,
        project_files=artifact["project_files"],
    )

    logger.info("Check run job=%d rc=%d user=%s", job_id, result.rc, user.get("username"))
    return CheckResult(
        rc=result.rc,
        changed=result.changed,
        diff=result.stdout[-10000:] if result.stdout else "",
        summary=result.summary,
        stderr=(result.stderr or "")[-8000:],  # So UI can show why dry-run failed
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
            "SELECT status, ansible_playbook, ansible_artifact_manifest, generated_config, rollback_config "
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

    artifact = _load_job_artifact(job_id)
    if not artifact["playbook_yaml"]:
        raise HTTPException(
            422,
            "No Ansible playbook stored for this job. Run /plan-ansible first."
        )

    from ansible.runner import run_playbook

    try:
        planning_context = (
            build_scoped_planning_context(
                _db,
                artifact["target_hosts"] or [],
                query=artifact["playbook_yaml"],
            )
            if _db is not None and hasattr(_db, "get_all_devices")
            else _minimal_planning_context(
                artifact["target_hosts"] or [],
                vendor="",
                intent_description=artifact["playbook_yaml"],
                feature="",
            )
        )
    except Exception:
        planning_context = _minimal_planning_context(
            artifact["target_hosts"] or [],
            vendor="",
            intent_description=artifact["playbook_yaml"],
            feature="",
        )
    inventory = _build_inventory_for_hosts(artifact["target_hosts"] or [], planning_context)

    # Mark running
    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'running', stage = 'apply' WHERE id = ?",
            (job_id,)
        )

    result = await run_in_threadpool(
        run_playbook,
        inventory=inventory,
        extra_vars={},
        check_mode=False,
        job_id=job_id,
        playbook_content=artifact["playbook_yaml"],
        playbook_path=artifact["entrypoint_path"] or None,
        project_files=artifact["project_files"],
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
            "SELECT id, status, stage, error_message, ansible_playbook, "
            "ansible_artifact_manifest, ansible_entrypoint_path "
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

    out = {
        "job_id":  job_id,
        "status":  row["status"],
        "stage":   row["stage"],
        "error":   row["error_message"],
        "has_playbook": bool(row["ansible_playbook"] or row["ansible_artifact_manifest"]),
        "entrypoint_path": row["ansible_entrypoint_path"],
        "artifact_type": "bundle" if row["ansible_artifact_manifest"] else "playbook",
        "steps":   [dict(r) for r in logs],
    }
    # Include bundle file list so chat/file UI can show all generated files
    if row["ansible_artifact_manifest"]:
        try:
            bundle = json.loads(row["ansible_artifact_manifest"])
            out["files"] = [{"path": f.get("path", "")} for f in (bundle.get("files") or []) if f.get("path")]
        except Exception:
            out["files"] = []
    else:
        out["files"] = []
    return out


@router.get("/jobs/{job_id}/inventory", summary="Get effective inventory for a job (hosts, IPs, credential source)")
async def get_job_inventory(
    job_id: int,
    user: dict = Depends(get_current_user),
):
    """
    Returns the list of hosts that will be targeted by this job, with management IP
    and credential source. No secrets are returned.
    """
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    artifact = _load_job_artifact(job_id)
    target_hosts = artifact.get("target_hosts") or []
    if not target_hosts:
        return {
            "job_id": job_id,
            "hosts": [],
            "credentials_note": "No target hosts in this job.",
        }

    planning_context = None
    if _db is not None:
        planning_context = await run_in_threadpool(
            build_scoped_planning_context,
            _db,
            target_hosts,
        )
    if not planning_context or not (planning_context.get("devices")):
        planning_context = _minimal_planning_context(target_hosts, vendor="")
    inventory = _build_inventory_for_hosts(target_hosts, planning_context)
    all_hosts = (inventory.get("all") or {}).get("hosts") or {}
    if isinstance(all_hosts, dict):
        host_list = []
        for hostname, vars in all_hosts.items():
            if not isinstance(vars, dict):
                vars = {}
            host_list.append({
                "hostname": hostname,
                "ansible_host": vars.get("ansible_host") or hostname,
                "vendor": vars.get("nettopo_vendor") or "",
                "role": vars.get("nettopo_role") or "",
                "credentials_source": _inventory_credentials_source(vars),
            })
        host_list.sort(key=lambda h: (h["hostname"],))
    else:
        host_list = []

    # Admin-only: show SSH username/password from local DB (devices.ssh_user, devices.ssh_password).
    # Blank values mean: device not in DB, or device row has NULL/empty ssh_user/ssh_password (e.g.
    # device was synced from NetBox, or credentials were never set in the NetBox controller form).
    if host_list and _db is not None and (user.get("role") == "admin"):
        for h in host_list:
            h["ssh_user"] = ""
            h["ssh_password"] = ""
            try:
                dev = _db.get_device_by_hostname(h["hostname"])
                if dev:
                    h["ssh_user"] = (dev.ssh_user or "").strip()
                    h["ssh_password"] = (dev.ssh_password or "").strip()
            except Exception:
                pass

    credentials_note = (
        "SSH username/password shown here (admin only) are from the local device inventory (devices table). "
        "Playbooks use Ansible Vault (vault/net_creds.yml) at run time."
    )
    return {
        "job_id": job_id,
        "hosts": host_list,
        "credentials_note": credentials_note,
    }


def _inventory_credentials_source(host_vars: dict) -> str:
    """Return a short, non-secret description of which vault vars this host uses."""
    vendor = (host_vars.get("nettopo_vendor") or "").replace(".", "_")
    if vendor:
        return f"vault (vendor: {vendor})"
    return "vault (shared)"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_job_playbook(job_id: int) -> tuple[str, list[str]]:
    """Load playbook YAML and target hosts from DB. Raises HTTPException on error."""
    artifact = _load_job_artifact(job_id)
    return artifact["playbook_yaml"], artifact["target_hosts"]


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

    requested_hosts = [str(h).strip() for h in (intent.get("target_hosts") or []) if str(h).strip()]
    planning_context = dict(intent.get("planning_context") or {})
    inventory = _build_inventory_for_hosts(requested_hosts, planning_context or None)
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


async def _validate_bundle_candidate(
    *,
    bundle: AnsibleArtifactBundle,
    intent: dict,
    job_id: int,
    verify_with_dry_run: bool,
) -> dict[str, Any]:
    builder = _require_builder()
    validation = builder.validate_bundle(intent=intent, bundle=bundle)
    issues = [dict(issue) for issue in validation.get("issues") or []]

    requested_hosts = [str(h).strip() for h in (intent.get("target_hosts") or []) if str(h).strip()]
    planning_context = dict(intent.get("planning_context") or {})
    inventory = _build_inventory_for_hosts(requested_hosts, planning_context or None)
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
            inventory=inventory,
            extra_vars={},
            check_mode=True,
            job_id=job_id,
            playbook_path=bundle.entrypoint_path,
            project_files=[artifact.to_dict() for artifact in bundle.files],
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


def _builder_for_request(
    provider: Optional[str],
    model: Optional[str],
    ollama_base_url: Optional[str],
):
    """Return a PlaybookBuilder for this request: request-scoped if provider/model set, else global."""
    if provider and str(provider).strip().lower() in ("gemini", "ollama"):
        from llm.api import _instantiate_runtime_provider
        from ansible.builder import PlaybookBuilder
        p = _instantiate_runtime_provider(
            str(provider).strip().lower(),
            model and str(model).strip() or None,
            ollama_base_url and str(ollama_base_url).strip() or None,
        )
        return PlaybookBuilder(p)
    return _require_builder()


async def _build_playbook_with_refinement(
    job_id: int,
    intent: dict,
    rag_context: str,
    kb_snippets: list[str],
    auto_refine: bool,
    max_attempts: int,
    verify_with_dry_run: bool,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
) -> dict[str, Any]:
    builder = _builder_for_request(provider, model, ollama_base_url)
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
            builder=builder,
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


async def _build_bundle_with_refinement(
    job_id: int,
    intent: dict,
    rag_context: str,
    kb_snippets: list[str],
    auto_refine: bool,
    max_attempts: int,
    verify_with_dry_run: bool,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
) -> dict[str, Any]:
    builder = _builder_for_request(provider, model, ollama_base_url)
    bounded_attempts = max(1, min(int(max_attempts or 1), 5))
    history: list[dict[str, Any]] = []
    extra_guidance = ""
    current_bundle = await builder.build_bundle_draft(
        intent=intent,
        rag_context=rag_context,
        kb_snippets=kb_snippets,
        run_id=str(job_id),
    )

    for attempt in range(1, bounded_attempts + 1):
        validation = await _validate_bundle_candidate(
            bundle=current_bundle,
            intent=intent,
            job_id=job_id,
            verify_with_dry_run=verify_with_dry_run,
        )
        history.append(
            {
                "attempt": attempt,
                "passed": validation.get("passed", False),
                "issues": validation.get("issues", []),
            }
        )
        if validation.get("passed", False):
            return {
                "success": True,
                "bundle": current_bundle.to_dict(),
                "playbook": current_bundle.file_map().get(current_bundle.normalized_entrypoint(), ""),
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
            }
        refinable_issues = [issue for issue in validation.get("issues", []) if issue.get("refinable", True)]
        if not auto_refine or not refinable_issues or attempt >= bounded_attempts:
            return {
                "success": False,
                "bundle": current_bundle.to_dict(),
                "playbook": current_bundle.file_map().get(current_bundle.normalized_entrypoint(), ""),
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
                "escalation_reason": "bundle_validation_failed",
            }
        extra_guidance = (
            "Previous bundle validation failed. Regenerate the full bundle and fix these issues only:\n"
            + "\n".join(f"- {item}" for item in validation.get("errors") or [])
        )
        current_bundle = await builder.build_bundle_draft(
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets,
            run_id=str(job_id),
            extra_guidance=extra_guidance,
        )

    return {
        "success": False,
        "bundle": current_bundle.to_dict(),
        "playbook": current_bundle.file_map().get(current_bundle.normalized_entrypoint(), ""),
        "attempts_used": bounded_attempts,
        "max_attempts": bounded_attempts,
        "final_validation": {"passed": False, "issues": [], "errors": []},
        "history": history,
        "escalation_reason": "max_refinement_attempts_reached",
    }


# =============================================================================
# M23 ─ Ansible Workflow Studio  (new endpoints)
# =============================================================================

ANSIBLE_BASE = Path(__file__).parent
_ALLOWED_ROOTS = ("playbooks", "roles", "inventory", "awx")
_WRITABLE_ROOTS = ("playbooks", "inventory")   # roles/awx are read-only from UI
_GENERATED_ROLE_ROOT = "roles/generated"
_GENERATED_PLAYBOOK_ROOT = "playbooks/generated"


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
    relative_norm = str(relative).replace("\\", "/").strip("/")
    allow_generated_role = relative_norm.startswith(f"{_GENERATED_ROLE_ROOT}/")
    if not rel_parts or (rel_parts[0] not in _WRITABLE_ROOTS and not allow_generated_role):
        raise HTTPException(
            403,
            f"Only {_WRITABLE_ROOTS} and {_GENERATED_ROLE_ROOT}/ are writable. "
            "Hand-maintained roles/ and awx/ remain read-only.",
        )
    return path, rel_parts


def _entrypoint_content(bundle: dict[str, Any]) -> str:
    entrypoint_path = str(bundle.get("entrypoint_path") or "").replace("\\", "/").strip("/")
    for item in bundle.get("files") or []:
        if str(item.get("path") or "").replace("\\", "/").strip("/") == entrypoint_path:
            return str(item.get("content") or "")
    return ""


def _entrypoint_content_from_disk(entrypoint_path: str) -> str | None:
    """
    If the entrypoint file exists on disk under the Ansible project, return its content.
    This allows manual edits (or accepted diffs) to the playbook file to be used
    by the UI and by Check/Deploy instead of the stored DB version.
    Returns None if path is empty, invalid, or file does not exist.
    """
    path_str = str(entrypoint_path or "").replace("\\", "/").strip("/")
    if not path_str:
        return None
    try:
        disk_path = _safe_path(path_str)
    except HTTPException:
        return None
    if not disk_path.is_file():
        return None
    try:
        return disk_path.read_text(encoding="utf-8")
    except Exception:
        return None


def _persist_bundle_to_workspace(bundle: dict[str, Any]) -> None:
    for item in bundle.get("files") or []:
        rel_path = str(item.get("path") or "").replace("\\", "/").strip("/")
        if not rel_path:
            continue
        path = _safe_path(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(item.get("content") or ""), encoding="utf-8")


def _bundle_target_hosts(bundle: dict[str, Any], fallback_playbook: str = "") -> list[str]:
    manifest = bundle.get("manifest") or {}
    hosts = manifest.get("target_hosts")
    if isinstance(hosts, list) and hosts:
        return [str(host).strip() for host in hosts if str(host).strip()]
    entrypoint_yaml = _entrypoint_content(bundle) or fallback_playbook
    if not entrypoint_yaml:
        return []
    try:
        import yaml

        plays = yaml.safe_load(entrypoint_yaml)
        hosts_value = plays[0].get("hosts", "") if plays else ""
        if isinstance(hosts_value, str) and hosts_value not in ("", "all"):
            return [item.strip() for item in hosts_value.split(",") if item.strip()]
    except Exception:
        return []
    return []


def _persist_job_bundle(
    *,
    job_id: int,
    bundle: dict[str, Any],
    playbook_yaml: str,
    status_value: str = "pending",
    stage_value: str = "plan",
) -> None:
    if _db is None:
        return
    _persist_bundle_to_workspace(bundle)
    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET ansible_playbook = ?, ansible_artifact_manifest = ?, "
            "ansible_entrypoint_path = ?, stage = ?, status = ? WHERE id = ?",
            (
                playbook_yaml,
                json.dumps(bundle),
                str(bundle.get("entrypoint_path") or ""),
                stage_value,
                status_value,
                job_id,
            ),
        )


def _load_job_artifact(job_id: int) -> dict[str, Any]:
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        row = conn.execute(
            "SELECT ansible_playbook, ansible_artifact_manifest, ansible_entrypoint_path "
            "FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

    if not row:
        raise HTTPException(404, f"Job {job_id} not found")

    bundle_raw = row["ansible_artifact_manifest"] if "ansible_artifact_manifest" in row.keys() else None
    if bundle_raw:
        try:
            bundle = bundle_from_dict(json.loads(bundle_raw)).to_dict()
        except Exception as exc:
            raise HTTPException(500, f"Stored Ansible bundle is invalid: {exc}")
        entrypoint_path = str(bundle.get("entrypoint_path") or "").replace("\\", "/").strip("/")
        playbook_yaml = _entrypoint_content(bundle)
        # Prefer disk content so manual/editor edits are used by UI and Check/Deploy
        disk_yaml = _entrypoint_content_from_disk(entrypoint_path)
        if disk_yaml is not None:
            playbook_yaml = disk_yaml
            # Update bundle's entrypoint file content so project_files (and runner) use disk version
            for item in bundle.get("files") or []:
                if str(item.get("path") or "").replace("\\", "/").strip("/") == entrypoint_path:
                    item["content"] = disk_yaml
                    break
        return {
            "mode": "bundle",
            "bundle": bundle,
            "playbook_yaml": playbook_yaml,
            "entrypoint_path": entrypoint_path or str(bundle.get("entrypoint_path") or ""),
            "target_hosts": _bundle_target_hosts(bundle),
            "project_files": list(bundle.get("files") or []),
        }

    playbook_yaml = row["ansible_playbook"]
    entrypoint_path = str(row["ansible_entrypoint_path"] or "").replace("\\", "/").strip("/")
    if not playbook_yaml and not entrypoint_path:
        raise HTTPException(422, "No Ansible playbook stored. Run /plan-ansible first.")
    # Prefer disk content when entrypoint path exists on disk
    disk_yaml = _entrypoint_content_from_disk(entrypoint_path) if entrypoint_path else None
    if disk_yaml is not None:
        playbook_yaml = disk_yaml
    elif not playbook_yaml:
        raise HTTPException(422, "No Ansible playbook stored. Run /plan-ansible first.")
    return {
        "mode": "playbook",
        "bundle": None,
        "playbook_yaml": playbook_yaml,
        "entrypoint_path": entrypoint_path or str(row["ansible_entrypoint_path"] or ""),
        "target_hosts": _bundle_target_hosts({}, playbook_yaml),
        "project_files": None,
    }


# ── M23: create-and-plan ───────────────────────────────────────────────────

class CreateAndPlanRequest(BaseModel):
    intent_description: str
    target_hosts:       List[str] = []
    vendor:             str = ""
    feature:            str = ""
    params:             Dict[str, Any] = {}
    device_id:          int = 0
    provider:           Optional[str] = None  # "gemini" | "ollama" for playbook LLM
    model:              Optional[str] = None
    ollama_base_url:    Optional[str] = None


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

    plan_req, parsed_intent, policy = _resolve_backend_intent_request(req, user)

    # Resolve device_id: if caller passes 0 / unknown, look up by first target host,
    # or fallback to first available device to avoid FK violations.
    device_id = req.device_id
    if device_id == 0:
        with _db._connect() as conn:
            # 1. Try first target host
            if plan_req.target_hosts:
                row = conn.execute(
                    "SELECT id FROM devices WHERE hostname = ? LIMIT 1",
                    (plan_req.target_hosts[0],),
                ).fetchone()
                if row:
                    device_id = row["id"]
            
            # 2. Fallback to any device if still not resolved
            if device_id == 0:
                row = conn.execute("SELECT id FROM devices ORDER BY id LIMIT 1").fetchone()
                if row:
                    device_id = row["id"]

    if device_id == 0:
        raise HTTPException(
            400, "Device inventory is empty. Please add a device before generating playbooks."
        )

    logger.info("create_and_plan: Resolved device_id=%d for hosts=%s", device_id, plan_req.target_hosts)

    # Create the change request + job
    from deploy.models import ChangeRequest, Job, DeploymentRepository
    repo = DeploymentRepository(_db)

    try:
        # 1. New CR
        cr_id = repo.create_request(ChangeRequest(
            title=f"Ansible Studio: {req.intent_description[:60]}",
            description=req.intent_description,
            requester=user.get("username", "ansible_studio"),
        ))
        logger.info("create_and_plan: Created ChangeRequest id=%d", cr_id)

        # 2. New Job linked to first device (for bookkeeping)
        job_id = repo.create_job(Job(request_id=cr_id, device_id=device_id))
        logger.info("create_and_plan: Created Job id=%d linked to device_id=%d", job_id, device_id)
    except Exception as exc:
        logger.error("create_and_plan: Database error during CR/Job creation: %s", exc)
        if "FOREIGN KEY" in str(exc).upper():
             # Detailed FK debug: check if IDs exist
             with _db._connect() as conn:
                 cr_exists = conn.execute("SELECT 1 FROM change_requests WHERE id=?", (cr_id if 'cr_id' in locals() else -1,)).fetchone()
                 dev_exists = conn.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone()
                 logger.error("FK Debug: device_id=%d exists=%s, cr_id=%s exists=%s", 
                              device_id, bool(dev_exists), 
                              cr_id if 'cr_id' in locals() else 'N/A', bool(cr_exists))
        raise HTTPException(status_code=500, detail=f"Database record creation failed: {exc}")

    # Delegate to plan_ansible logic (pass through LLM provider/model for playbook generation)
    result = await plan_ansible(job_id=job_id, req=plan_req, user=user)
    return {
        "job_id":      job_id,
        "request_id":  cr_id,
        "status":      result.get("status"),
        "playbook":    result.get("playbook"),
        "artifact_type": result.get("artifact_type"),
        "entrypoint_path": result.get("entrypoint_path"),
        "files": result.get("files", []),
        "manifest": result.get("manifest", {}),
        "parsed_intent": parsed_intent,
        "policy": policy,
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
    if device_id == 0:
        with _db._connect() as conn:
            if req.target_hosts:
                row = conn.execute(
                    "SELECT id FROM devices WHERE hostname = ? LIMIT 1",
                    (req.target_hosts[0],),
                ).fetchone()
                if row:
                    device_id = row["id"]
            
            if device_id == 0:
                row = conn.execute("SELECT id FROM devices ORDER BY id LIMIT 1").fetchone()
                if row:
                    device_id = row["id"]

    if device_id == 0:
        raise HTTPException(
            400, "Device inventory is empty. Please add a device before using templates."
        )

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
    # Only playbooks/, inventory/, and generated roles/ are writable from the UI
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
        raise HTTPException(400, "Provide a file path under playbooks/, inventory/, or roles/generated/")
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
        raise HTTPException(400, "Provide a folder path under playbooks/, inventory/, or roles/generated/")
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
    builder: Any = None,
) -> tuple[str, list[str]]:
    """Ask the LLM to fix a playbook given error/feedback."""
    if builder is None and _agent is None and _builder is None:
        raise HTTPException(503, "LLM not available for playbook refinement")

    # Prefer request-scoped builder, then agent, then global builder
    provider = None
    if builder is not None and hasattr(builder, "_provider"):
        provider = builder._provider
    elif _agent is not None:
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
    bundle: Optional[dict[str, Any]] = None,
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
        "entrypoint_path":  (bundle or {}).get("entrypoint_path"),
        "artifact_type":    "bundle" if bundle else "playbook",
        "file_count":       len((bundle or {}).get("files", [])),
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "refinement_notes": notes,
    })

    try:
        with db._connect() as conn:
            if set_current:
                if bundle:
                    conn.execute(
                        "UPDATE jobs SET playbook_versions=?, ansible_playbook=?, "
                        "ansible_artifact_manifest=?, ansible_entrypoint_path=? WHERE id=?",
                        (
                            json.dumps(versions),
                            playbook,
                            json.dumps(bundle),
                            str(bundle.get("entrypoint_path") or ""),
                            job_id,
                        ),
                    )
                else:
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
    artifact_type:    str = "playbook"
    entrypoint_path:  Optional[str] = None
    file_count:       int = 0


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