from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from starlette.concurrency import run_in_threadpool

from db.manager import DatabaseManager
from db.models import ConfigSnapshot
from drivers.factory import DriverFactory

from .policy_service import PolicyService
from .evaluator import ComplianceEvaluator
from .scope_resolver import NetBoxScopeResolver

logger = logging.getLogger("compliance.api")

DB_PATH = "db/netauto.db"
router = APIRouter()


def _db() -> DatabaseManager:
    return DatabaseManager(DB_PATH)


def _policy_or_404(db: DatabaseManager, policy_id: str) -> Dict[str, Any]:
    p = db.get_policy_by_policy_id(policy_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
    return p


# ─────────────────────────────────────────────────────────────
# Policies CRUD + audit + clone + test
# ─────────────────────────────────────────────────────────────
@router.get("/policies")
async def list_policies(include_disabled: bool = True):
    db = _db()
    items = await run_in_threadpool(db.list_policies, include_disabled)
    return {"items": items, "total": len(items)}


@router.post("/policies")
async def create_policy(body: Dict[str, Any]):
    svc = PolicyService()
    vr = svc.validate_and_normalize(body or {})
    if not vr.ok:
        raise HTTPException(status_code=422, detail={"message": "policy_validation_failed", "issues": vr.issues})
    db = _db()
    row = await run_in_threadpool(db.create_policy, vr.normalized, "frontend-user")
    p = db.get_policy_by_policy_id(vr.normalized["policy_id"])
    return {"policy": p, "row_id": row.get("id") if isinstance(row, dict) else None}


@router.get("/policies/{policy_id}")
async def get_policy(policy_id: str):
    db = _db()
    p = await run_in_threadpool(db.get_policy_by_policy_id, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="not_found")
    return {"policy": p}


@router.put("/policies/{policy_id}")
async def update_policy(policy_id: str, body: Dict[str, Any]):
    db = _db()
    cur = await run_in_threadpool(db.get_policy_by_policy_id, policy_id)
    if not cur:
        raise HTTPException(status_code=404, detail="not_found")
    merged = dict(cur)
    merged.update({k: v for k, v in (body or {}).items() if k != "policy_id"})
    svc = PolicyService()
    vr = svc.validate_and_normalize(merged)
    if not vr.ok:
        raise HTTPException(status_code=422, detail={"message": "policy_validation_failed", "issues": vr.issues})
    out = await run_in_threadpool(db.update_policy, policy_id, vr.normalized, "frontend-user")
    if not out:
        raise HTTPException(status_code=404, detail="not_found")
    return {"policy": out}


@router.delete("/policies/{policy_id}")
async def delete_policy(policy_id: str):
    db = _db()
    ok = await run_in_threadpool(db.delete_policy, policy_id, "frontend-user")
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    return {"deleted": True, "policy_id": policy_id}


@router.get("/policies/{policy_id}/audit")
async def list_policy_audit(policy_id: str, limit: int = 50):
    db = _db()
    items = await run_in_threadpool(db.list_policy_audit, policy_id, limit)
    return {"items": items, "total": len(items)}


@router.post("/policies/{policy_id}/clone")
async def clone_policy(policy_id: str, body: Optional[Dict[str, Any]] = None):
    db = _db()
    p = _policy_or_404(db, policy_id)
    suffix = str((body or {}).get("suffix") or "clone").strip() or "clone"
    clone_obj = dict(p)
    clone_obj["policy_id"] = f"{p['policy_id']}_{suffix}".lower()[:80]
    clone_obj["name"] = f"{p.get('name','')} ({suffix})".strip()
    clone_obj["enabled"] = bool((body or {}).get("enabled", True))
    clone_obj.pop("id", None)
    clone_obj.pop("version", None)
    clone_obj.pop("created_at", None)
    clone_obj.pop("updated_at", None)
    row = await run_in_threadpool(db.create_policy, clone_obj, "frontend-user")
    created = db.get_policy_by_policy_id(clone_obj["policy_id"])
    return {"policy": created, "row_id": row.get("id") if isinstance(row, dict) else None}


@router.post("/policies/{policy_id}/test")
async def test_policy(policy_id: str, body: Dict[str, Any]):
    """Test a policy against a snapshot or latest snapshot for a device.

    Note: richer normalized evaluation is implemented in engine-core; this is an MVP.
    """
    db = _db()
    p = _policy_or_404(db, policy_id)
    snapshot_id = body.get("snapshot_id")
    device_id = body.get("device_id")

    snap: Optional[ConfigSnapshot] = None
    if snapshot_id is not None:
        snap = await run_in_threadpool(db.get_config_snapshot, int(snapshot_id))
    elif device_id is not None:
        hist = await run_in_threadpool(db.get_config_history, int(device_id), 1)
        snap = hist[0] if hist else None

    if not snap:
        raise HTTPException(status_code=404, detail="snapshot_not_found")

    text = snap.config_text or ""
    rules = p.get("rules") or []
    violations: List[Dict[str, Any]] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        action = str(r.get("action") or "").strip().lower()
        target = r.get("target") or r.get("path")
        if not target:
            continue
        sev = str(r.get("severity") or "medium").lower()
        needle = str(target)
        if action in ("must_exist", "must_contain"):
            if needle not in text:
                violations.append({"policy_id": p["policy_id"], "rule_id": r.get("id"), "severity": sev, "message": f"missing: {needle}"})
        elif action in ("must_not_exist", "must_be_absent"):
            if needle in text:
                violations.append({"policy_id": p["policy_id"], "rule_id": r.get("id"), "severity": sev, "message": f"forbidden present: {needle}"})
        elif action == "must_match_regex":
            try:
                import re

                if not re.search(str(needle), text, re.M):
                    violations.append({"policy_id": p["policy_id"], "rule_id": r.get("id"), "severity": sev, "message": f"regex not matched: {needle}"})
            except Exception:
                violations.append({"policy_id": p["policy_id"], "rule_id": r.get("id"), "severity": "low", "message": f"invalid regex: {needle}"})

    return {"policy_id": p["policy_id"], "snapshot_id": snap.id, "device_id": snap.device_id, "status": "violated" if violations else "compliant", "violations": violations}


# ─────────────────────────────────────────────────────────────
# Snapshots & baseline APIs
# ─────────────────────────────────────────────────────────────
@router.get("/devices/{device_id}/baseline")
async def get_device_baseline(device_id: int):
    db = _db()
    snap = await run_in_threadpool(db.get_latest_baseline_snapshot, device_id)
    if not snap:
        return {"baseline": None}
    return {
        "baseline": {
            "snapshot_id": snap.id,
            "device_id": snap.device_id,
            "captured_at": snap.captured_at,
            "snapshot_type": getattr(snap, "snapshot_type", "baseline"),
            "source_type": getattr(snap, "source_type", None),
            "approved_by": getattr(snap, "approved_by", None),
            "verified_by": getattr(snap, "verified_by", None),
            "verified_at": getattr(snap, "verified_at", None),
            "policy_version": getattr(snap, "policy_version", None),
            "intent_id": getattr(snap, "intent_id", None),
            "execution_id": getattr(snap, "execution_id", None),
        }
    }


@router.post("/devices/{device_id}/baseline/set")
async def set_device_baseline(device_id: int, body: Dict[str, Any]):
    snapshot_id = body.get("snapshot_id")
    if snapshot_id is None:
        raise HTTPException(status_code=422, detail="snapshot_id is required")
    db = _db()
    snap = await run_in_threadpool(db.get_config_snapshot, int(snapshot_id))
    if not snap or snap.device_id != device_id:
        raise HTTPException(status_code=404, detail="snapshot_not_found")
    with db._connect() as conn:
        conn.execute(
            "UPDATE config_snapshots SET snapshot_type = 'baseline', source_type = COALESCE(source_type,'manual') WHERE id = ?",
            (int(snapshot_id),),
        )
    return {"status": "ok", "device_id": device_id, "baseline_snapshot_id": int(snapshot_id)}


@router.get("/devices/{device_id}/snapshots")
async def list_device_snapshots(device_id: int, limit: int = 50):
    db = _db()
    snaps = await run_in_threadpool(db.get_config_history, device_id, limit)
    items = [
        {
            "id": s.id,
            "device_id": s.device_id,
            "captured_at": s.captured_at,
            "snapshot_type": getattr(s, "snapshot_type", "snapshot"),
            "source_type": getattr(s, "source_type", None),
            "config_size": len(s.config_text or ""),
        }
        for s in snaps
    ]
    return {"items": items, "total": len(items)}


@router.post("/devices/{device_id}/snapshots/collect")
async def collect_device_snapshot(device_id: int):
    db = _db()
    dev = await run_in_threadpool(db.get_device_by_id, device_id)
    if not dev:
        raise HTTPException(status_code=404, detail="device_not_found")
    try:
        driver = DriverFactory.get_driver(dev)
        driver.connect()
        cfg = driver.get_running_config()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"collect_failed: {exc}")
    finally:
        try:
            driver.disconnect()  # type: ignore[union-attr]
        except Exception:
            pass
    snap_id = await run_in_threadpool(
        db.save_config_snapshot,
        ConfigSnapshot(device_id=device_id, config_text=cfg or "", snapshot_type="actual", source_type="scan"),
    )
    return {"status": "ok", "snapshot_id": snap_id, "device_id": device_id}


# ─────────────────────────────────────────────────────────────
# Compliance scan + results + summary + alerts
# ─────────────────────────────────────────────────────────────
def _emit_compliance_alert_if_needed(db: DatabaseManager, device_id: int, result: Dict[str, Any]) -> None:
    sev = str(result.get("severity") or "").lower()
    if sev not in ("high", "critical"):
        return
    try:
        db.insert_security_event(
            event_type="compliance_drift",
            severity="high" if sev == "high" else "critical",
            description=f"Compliance drift on device_id={device_id} status={result.get('status')}",
            device_id=device_id,
            raw_data=None,
        )
    except Exception as exc:
        logger.debug("Compliance alert emit failed: %s", exc)


def _compute_device_compliance_mvp(db: DatabaseManager, device_id: int) -> Dict[str, Any]:
    """Compliance compute (policy-driven baseline comparison).

    Per docs/policy_compliance_drift.md:
    - never computes without a baseline
    - always normalize before diff
    - diff baseline vs actual into missing/extra/changed
    - evaluate policies separately
    - compute score + severity + reasoned status
    """
    dev = db.get_device_by_id(device_id)
    if not dev:
        return {"device_id": device_id, "status": "no_actual", "reason": "device_not_found"}

    baseline = db.get_latest_baseline_snapshot(device_id)
    if not baseline:
        return {"device_id": device_id, "status": "no_baseline", "reason": "no baseline snapshot"}

    try:
        driver = DriverFactory.get_driver(dev)
        driver.connect()
        actual_cfg = driver.get_running_config() or ""
    except Exception as exc:
        return {"device_id": device_id, "status": "unreachable", "reason": f"collect_failed: {exc}"}
    finally:
        try:
            driver.disconnect()  # type: ignore[union-attr]
        except Exception:
            pass

    # Load policies once (engine will scope-filter)
    policies = db.list_policies(include_disabled=False)
    evaluator = ComplianceEvaluator()
    # NetBox-as-SoT device scope attributes (hybrid cached resolver)
    scope = NetBoxScopeResolver().resolve_device(db, device_id)
    dev_dict = dict(scope.device or {})

    ev = evaluator.evaluate_device(
        device=dev_dict,
        baseline_raw=baseline.config_text or "",
        actual_raw=actual_cfg,
        vendor=dev.vendor,
        policies=policies,
    )

    # Store actual snapshot with normalized_config + checksum
    actual_snapshot_id = db.save_config_snapshot(
        ConfigSnapshot(
            device_id=device_id,
            config_text=actual_cfg,
            snapshot_type="actual",
            source_type="scan",
            normalized_config=(ev.get("actual_normalized") or {}).get("text"),
            checksum=None,
        )
    )

    # Opportunistically backfill baseline normalized_config if missing (non-destructive)
    try:
        if not getattr(baseline, "normalized_config", None) and (ev.get("baseline_normalized") or {}).get("text"):
            with db._connect() as conn:
                conn.execute(
                    "UPDATE config_snapshots SET normalized_config = ?, checksum = COALESCE(checksum, ?) WHERE id = ?",
                    (
                        (ev.get("baseline_normalized") or {}).get("text"),
                        baseline.checksum,
                        int(baseline.id),
                    ),
                )
    except Exception:
        pass

    result_row = {
        "device_id": device_id,
        "baseline_snapshot_id": baseline.id,
        "actual_snapshot_id": actual_snapshot_id,
        "status": ev.get("status"),
        "reason": ev.get("reason"),
        "compliance_score": ev.get("compliance_score"),
        "severity": ev.get("severity"),
        "drift_pct": ev.get("drift_pct"),
        "missing_lines": ev.get("missing_lines") or [],
        "extra_lines": ev.get("extra_lines") or [],
        "changed_lines": ev.get("changed_lines") or [],
        "violated_policies": ev.get("violated_policies") or [],
    }
    db.insert_compliance_result(result_row)
    _emit_compliance_alert_if_needed(db, device_id, result_row)
    return result_row


@router.get("/compliance/scope/options")
async def compliance_scope_options():
    """NetBox-driven options for policy scope UI (cached best-effort)."""
    r = NetBoxScopeResolver().scope_options()
    return r


def _scan_all_devices_job(job_id: int) -> None:
    db = _db()
    devices = db.get_all_devices()
    summary = {"devices_total": len(devices), "devices_ok": 0, "devices_failed": 0, "status_counts": {}}
    try:
        for d in devices:
            try:
                r = _compute_device_compliance_mvp(db, int(d.id))
                summary["devices_ok"] += 1
                st = r.get("status") or "unknown"
                summary["status_counts"][st] = int(summary["status_counts"].get(st, 0)) + 1
            except Exception:
                summary["devices_failed"] += 1
        db.finish_compliance_job(job_id, "success", summary=summary)
    except Exception as exc:
        db.finish_compliance_job(job_id, "failed", summary=summary, error_text=str(exc))


@router.post("/compliance/scan")
async def scan_all(background_tasks: BackgroundTasks):
    db = _db()
    job_id = await run_in_threadpool(db.create_compliance_job, "manual")
    background_tasks.add_task(_scan_all_devices_job, job_id)
    return {"status": "started", "job_id": job_id}


@router.post("/compliance/scan/{device_id}")
async def scan_device(device_id: int):
    db = _db()
    r = await run_in_threadpool(_compute_device_compliance_mvp, db, device_id)
    return {"result": r}


@router.get("/compliance/results")
async def list_latest_results(limit: int = 200):
    db = _db()
    items = await run_in_threadpool(db.get_latest_compliance_results, limit)
    return {"items": items, "total": len(items)}


@router.get("/compliance/results/{device_id}")
async def device_results(device_id: int, limit: int = 20):
    db = _db()
    items = await run_in_threadpool(db.get_device_compliance_history, device_id, limit)
    return {"items": items, "total": len(items)}


@router.get("/compliance/summary")
async def compliance_summary():
    db = _db()
    items = await run_in_threadpool(db.get_latest_compliance_results, 1000)
    counts = {
        "total": len(items),
        "compliant": 0,
        "drifted": 0,
        "violated": 0,
        "no_baseline": 0,
        "no_actual": 0,
        "unreachable": 0,
        "partial": 0,
    }
    sev_counts: Dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for r in items:
        st = str(r.get("status") or "").lower()
        if st in counts:
            counts[st] += 1
        sev = str(r.get("severity") or "low").lower()
        if sev in sev_counts:
            sev_counts[sev] += 1
    compliant = counts["compliant"]
    total = counts["total"] or 1
    compliance_pct = round((compliant / total) * 100, 1)
    return {"counts": counts, "severity_breakdown": sev_counts, "compliance_pct": compliance_pct}


@router.get("/compliance/alerts")
async def list_compliance_alerts(limit: int = 50):
    # Bridge to security_events for now
    db = _db()
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM security_events WHERE event_type = 'compliance_drift' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("/compliance/alerts/{alert_id}/ack")
async def ack_compliance_alert(alert_id: int):
    db = _db()
    with db._connect() as conn:
        cur = conn.execute("UPDATE security_events SET status = 'investigating' WHERE id = ?", (alert_id,))
        if cur.rowcount <= 0:
            raise HTTPException(status_code=404, detail="not_found")
    return {"status": "ok", "alert_id": alert_id}

