"""
Intent-validation regression runner.

Runs two stages per prompt case:
1) Preview intent: POST /api/v1/ansible/preview-intent
2) (Optional) Create-and-plan: POST /api/v1/ansible/create-and-plan

It consumes:
  - backend/ansible/intent_prompt_cases.yaml
  - backend/ansible/intent_prompt_golden.yaml
and produces per-case logs for later tuning.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = REPO_ROOT / "ansible" / "intent_prompt_cases.yaml"
GOLDEN_PATH = REPO_ROOT / "ansible" / "intent_prompt_golden.yaml"


@dataclass
class RunnerConfig:
    base_url: str
    provider: str
    model: str
    ollama_base_url: Optional[str]
    timeout_s: int
    log_dir: Path
    dry_run: bool
    max_disambiguation_rounds: int
    verify_with_dry_run: bool


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_dotenv_if_present() -> None:
    # Optional: load backend/.env so we can default OLLAMA_BASE_URL from it.
    # python-dotenv is a dependency in backend/requirements.txt.
    try:
        from dotenv import load_dotenv  # type: ignore

        dotenv_path = REPO_ROOT / ".env"
        if dotenv_path.is_file():
            load_dotenv(str(dotenv_path), override=False)
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _extract_preview_missing_fields_from_422(err_detail: Any) -> List[str]:
    missing = []
    if isinstance(err_detail, dict):
        mf = err_detail.get("missing_fields")
        if isinstance(mf, list):
            missing = [str(x) for x in mf if x is not None]
    return missing


def _collect_disambiguation_fields(preview_json: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    pdis = preview_json.get("params_disambiguation")
    if isinstance(pdis, dict):
        fields = pdis.get("fields")
        if isinstance(fields, list):
            for f in fields:
                if isinstance(f, dict) and f.get("field"):
                    out.append(str(f["field"]))
    return out


def _any_disambiguation_kind_present(preview_json: Dict[str, Any]) -> bool:
    for k in (
        "disambiguation",
        "devices_disambiguation",
        "interfaces_disambiguation",
        "params_disambiguation",
    ):
        v = preview_json.get(k)
        if v is not None:
            # Some payloads use {kind,...} and others use null.
            return True
    return False


def _fields_contains(actual: List[str], expected_contains: List[str]) -> bool:
    a = {str(x) for x in actual}
    for e in expected_contains:
        if str(e) not in a:
            return False
    return True


def _fields_forbidden_contains(actual: List[str], forbidden_contains: List[str]) -> bool:
    a = {str(x) for x in actual}
    for f in forbidden_contains:
        if str(f) in a:
            return False
    return True


def _preview_request_payload(
    *,
    case: Dict[str, Any],
    cfg: RunnerConfig,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # feature is a strong hint for schema/action selection.
    # category values map to schema feature keys (vlan/interface/acl/qos/static_route/ospf).
    payload: Dict[str, Any] = {
        "intent_description": str(case.get("intent_description") or ""),
        "feature": str(case.get("category") or ""),
        "vendor": "",
        "provider": cfg.provider,
        "model": cfg.model,
    }
    if cfg.ollama_base_url:
        payload["ollama_base_url"] = cfg.ollama_base_url
    if params is not None:
        payload["params"] = params
    return payload


def preview_intent(case: Dict[str, Any], cfg: RunnerConfig, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    url = cfg.base_url.rstrip("/") + "/api/v1/ansible/preview-intent"
    payload = _preview_request_payload(case=case, cfg=cfg, params=params)
    return requests.post(url, json=payload, timeout=cfg.timeout_s)


def create_and_plan(
    case: Dict[str, Any],
    cfg: RunnerConfig,
    parsed_intent: Dict[str, Any],
    target_hosts_override: Optional[List[str]] = None,
) -> requests.Response:
    url = cfg.base_url.rstrip("/") + "/api/v1/ansible/create-and-plan"
    feature = str(case.get("category") or "")
    vendor = str(parsed_intent.get("vendor") or "")
    resolved_devices = (
        target_hosts_override
        or parsed_intent.get("resolved_devices")
        or parsed_intent.get("target_hosts")
        or []
    )
    if isinstance(resolved_devices, str):
        resolved_devices = [resolved_devices]
    target_hosts = [str(x) for x in resolved_devices if str(x).strip()]
    known_prefixes = ("Acc_", "Core_", "Dist_")
    target_hosts = [h for h in target_hosts if isinstance(h, str) and h.startswith(known_prefixes)]

    params = parsed_intent.get("parameters") or {}

    # If the LLM produced an overly broad target set, narrow it using device_deduction hints.
    # In particular, we prefer entries whose reason says they were matched directly from prompt wording.
    try:
        dd = params.get("device_deduction") or {}
        mapping = dd.get("mapping")
        if isinstance(mapping, list):
            selected: List[str] = []
            for m in mapping:
                if not isinstance(m, dict):
                    continue
                hostname = m.get("hostname")
                reason = str(m.get("reason") or "")
                if not isinstance(hostname, str):
                    continue
                if any(hostname.startswith(p) for p in known_prefixes) and "matched prompt number" in reason:
                    if hostname not in selected:
                        selected.append(hostname)
            if selected:
                target_hosts = selected
    except Exception:
        pass

    # LLMs sometimes produce interface strings like "Acc_Cisco_3:Ethernet0/1".
    # The backend may interpret the left side as a *device hint* and require
    # interfaces_disambiguation / target_hosts reconciliation.
    # For regression playbook generation we want to treat the value as a plain
    # interface name, so we strip any "hostname:" prefix.
    interfaces = params.get("interfaces") or []
    if isinstance(interfaces, list):
        sanitized: List[Any] = []
        for it in interfaces:
            if isinstance(it, str):
                iface = it
                if ":" in iface:
                    iface = iface.split(":", 1)[1].strip()
                sanitized.append(iface)
            elif isinstance(it, dict):
                obj = dict(it)
                name_val = obj.get("name")
                if isinstance(name_val, str) and ":" in name_val:
                    obj["name"] = name_val.split(":", 1)[1].strip()
                sanitized.append(obj)
            else:
                sanitized.append(it)
        params["interfaces"] = sanitized

    # create-and-plan parses the intent again; we pass vendor/feature+params to reduce drift.
    payload: Dict[str, Any] = {
        "intent_description": str(case.get("intent_description") or ""),
        "target_hosts": target_hosts,
        "vendor": vendor,
        "feature": feature,
        "params": params,
        "device_id": 0,
        "provider": cfg.provider,
        "model": cfg.model,
        # Ensure we never deploy: only Ansible check-mode if enabled.
        "verify_with_dry_run": cfg.verify_with_dry_run,
        # Speed: we only need deterministic validation, not iterative refinement.
        "auto_refine": False,
        "max_refinement_attempts": 1,
    }
    if cfg.ollama_base_url:
        payload["ollama_base_url"] = cfg.ollama_base_url
    return requests.post(url, json=payload, timeout=cfg.timeout_s)


def _assert_preview_against_golden(
    *,
    case_id: str,
    expected: Dict[str, Any],
    resp: requests.Response,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    notes: List[str] = []
    actual_json: Dict[str, Any] = {}

    preview_status_expected = expected.get("preview_status")
    if preview_status_expected is not None:
        actual_status = resp.status_code
        if int(actual_status) != int(preview_status_expected):
            notes.append(f"HTTP status mismatch: expected {preview_status_expected}, got {actual_status}")
            return False, notes, actual_json

    try:
        if resp.content:
            actual_json = resp.json()
    except Exception:
        actual_json = {}

    # expected params disambiguation fields contains
    if expected.get("expected_categories_contains"):
        cats = actual_json.get("categories") if isinstance(actual_json, dict) else None
        actual_cats = [str(x) for x in (cats or []) if str(x).strip()]
        exp_cats = [str(x) for x in (expected.get("expected_categories_contains") or []) if str(x).strip()]
        if not _fields_contains(actual_cats, exp_cats):
            notes.append(
                f"categories mismatch: expected to contain {exp_cats}, actual={actual_cats}"
            )
            return False, notes, actual_json

    if expected.get("expected_action_in"):
        parsed = actual_json.get("parsed_intent") if isinstance(actual_json, dict) else None
        action = str((parsed or {}).get("action") or "").strip()
        expected_actions = [str(x).strip() for x in (expected.get("expected_action_in") or []) if str(x).strip()]
        if action not in expected_actions:
            notes.append(
                f"action mismatch: expected one of {expected_actions}, actual={action or '(empty)'}"
            )
            return False, notes, actual_json

    if expected.get("expected_params_disambiguation_fields_contains"):
        actual_fields = _collect_disambiguation_fields(actual_json)
        exp_fields = expected.get("expected_params_disambiguation_fields_contains") or []
        if not _fields_contains(actual_fields, [str(x) for x in exp_fields]):
            notes.append(
                f"params_disambiguation fields missing: expected to contain {exp_fields}, actual={actual_fields}"
            )
            return False, notes, actual_json

    if expected.get("expected_params_disambiguation_fields_forbidden_contains"):
        actual_fields = _collect_disambiguation_fields(actual_json)
        forbidden = expected.get("expected_params_disambiguation_fields_forbidden_contains") or []
        if not _fields_forbidden_contains(actual_fields, [str(x) for x in forbidden]):
            notes.append(
                f"params_disambiguation fields forbidden present: forbidden={forbidden}, actual={actual_fields}"
            )
            return False, notes, actual_json

    if expected.get("expected_any_disambiguation_kind") is True:
        if not _any_disambiguation_kind_present(actual_json):
            notes.append("Expected any disambiguation kind, but none present.")
            return False, notes, actual_json

    if expected.get("expected_missing_fields_contains"):
        # 422 error detail shape is: {detail: {missing_fields: [...], questions:[...]}}
        if resp.status_code != 422:
            notes.append(f"Expected 422 for missing_fields, got {resp.status_code}")
            return False, notes, actual_json
        try:
            err = resp.json()
        except Exception:
            err = {}
        detail = err.get("detail") if isinstance(err, dict) else None
        missing_fields = _extract_preview_missing_fields_from_422(detail)
        exp_missing = expected.get("expected_missing_fields_contains") or []
        if not _fields_contains(missing_fields, [str(x) for x in exp_missing]):
            notes.append(f"422 missing_fields mismatch: expected_contains={exp_missing}, actual={missing_fields}")
            return False, notes, actual_json

    if expected.get("expected_validation_errors_contains"):
        # validation errors are usually attached to preview_json.parsed_intent.validation_errors
        # Try best effort.
        parsed = actual_json.get("parsed_intent") if isinstance(actual_json, dict) else None
        ve = []
        if isinstance(parsed, dict):
            vlist = parsed.get("validation_errors")
            if isinstance(vlist, list):
                ve = [str(x) for x in vlist]
        for s in expected.get("expected_validation_errors_contains") or []:
            if not any(str(s) in x for x in ve):
                notes.append(f"validation_errors missing substring: {s}. actual={ve}")
                return False, notes, actual_json

    return True, notes, actual_json


def _assert_playbook_contains(
    *,
    case_id: str,
    expected: Dict[str, Any],
    resp: requests.Response,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    notes: List[str] = []
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {}

    if resp.status_code != 200:
        notes.append(f"create-and-plan HTTP status mismatch: {resp.status_code}")
        return False, notes, data

    playbook = data.get("playbook") or ""
    if not playbook:
        notes.append("create-and-plan response missing playbook")
        return False, notes, data

    must_contain = expected.get("must_contain") or []
    for snippet in must_contain:
        if str(snippet) not in playbook:
            notes.append(f"playbook missing required snippet: {snippet}")
            return False, notes, data
    return True, notes, data


def run_case(case: Dict[str, Any], golden: Dict[str, Any], cfg: RunnerConfig) -> Dict[str, Any]:
    case_id = str(case.get("id") or "")
    started = datetime.utcnow().isoformat()
    log: Dict[str, Any] = {
        "case_id": case_id,
        "started_at": started,
        "intent_description": case.get("intent_description"),
    }

    expected = golden.get("cases", {}).get(case_id, {})
    expected_preview = expected.get("expected_preview") or {}
    stageB = expected.get("stageB") or {}
    stageB_enabled = bool(stageB.get("enabled"))
    stageB_must_contain = stageB.get("must_contain") or []

    # Stage A: preview with optional params loop
    try:
        preview_resp = preview_intent(case=case, cfg=cfg, params=None)
    except Exception as e:
        log["preview_initial_error"] = {"error": str(e), "type": type(e).__name__}
        log["finished_at"] = datetime.utcnow().isoformat()
        log["result_ok"] = False
        return log
    log["preview_initial"] = {
        "status_code": preview_resp.status_code,
        "text": preview_resp.text[:2000],
    }

    ok_preview, notes_preview, preview_json = _assert_preview_against_golden(
        case_id=case_id,
        expected=expected_preview,
        resp=preview_resp,
    )
    log["preview_initial_assert"] = {"ok": ok_preview, "notes": notes_preview}

    # If preview expects 200 + params_disambiguation and stageB is enabled, we auto-fill via golden.auto_answers.
    # This keeps the runner aligned with the plan.
    if stageB_enabled and preview_resp.status_code == 200 and expected.get("auto_answers"):
        rounds = 0
        parsed_intent = None
        while rounds < cfg.max_disambiguation_rounds:
            rounds += 1
            try:
                pj = preview_json if isinstance(preview_json, dict) else {}
                parsed_intent = pj.get("parsed_intent")
            except Exception:
                parsed_intent = None

            if not isinstance(preview_json, dict):
                break

            pdis = preview_json.get("params_disambiguation")
            if not pdis:
                break

            fields = pdis.get("fields") or []
            # Build params from required fields present in disambiguation.
            answers = expected.get("auto_answers") or {}
            next_params: Dict[str, Any] = {}
            missing_auto = []
            if isinstance(fields, list):
                for f in fields:
                    if isinstance(f, dict) and f.get("field"):
                        key = str(f["field"])
                        if key in answers:
                            next_params[key] = answers[key]
                        else:
                            missing_auto.append(key)

            if missing_auto:
                log["auto_fill_skipped"] = {
                    "missing_auto_answers_for_fields": missing_auto,
                    "round": rounds,
                }
                break

            log.setdefault("preview_param_rounds", []).append(
                {"round": rounds, "params_sent": next_params}
            )
            if not cfg.dry_run:
                try:
                    preview_resp2 = preview_intent(case=case, cfg=cfg, params=next_params)
                except Exception as e:
                    log["preview_round_error"] = {"error": str(e), "type": type(e).__name__}
                    break
                preview_json = preview_resp2.json() if preview_resp2.content else {}
                log["preview_after_round_%d" % rounds] = {
                    "status_code": preview_resp2.status_code,
                    "text": preview_resp2.text[:2000],
                }
                # If after params disambiguation disappears, we can attempt stage B.
                pdis_after = isinstance(preview_json, dict) and preview_json.get("params_disambiguation")
                if not pdis_after:
                    break
            else:
                break

    # Stage B
    playbook_ok = True
    if stageB_enabled:
        # Best-effort: re-fetch latest preview json if we already got it.
        if not isinstance(preview_json, dict):
            try:
                preview_json = preview_resp.json()
            except Exception:
                preview_json = {}

        parsed_intent = preview_json.get("parsed_intent") if isinstance(preview_json, dict) else None
        if not isinstance(parsed_intent, dict):
            log["stageB_error"] = "No parsed_intent found after preview."
            playbook_ok = False
        else:
            if cfg.dry_run:
                log["stageB_skipped"] = "dry_run"
            else:
                try:
                    create_resp = create_and_plan(
                        case=case,
                        cfg=cfg,
                        parsed_intent=parsed_intent,
                        target_hosts_override=preview_json.get("target_hosts") if isinstance(preview_json, dict) else None,
                    )
                except Exception as e:
                    log["create_and_plan_error"] = {"error": str(e), "type": type(e).__name__}
                    playbook_ok = False
                    log["stageB_assert"] = {"ok": False, "notes": [str(e)]}
                else:
                    log["create_and_plan"] = {
                        "status_code": create_resp.status_code,
                        "text": create_resp.text[:2000],
                    }
                    expected_for_assert = {"must_contain": stageB_must_contain}
                    play_ok, notes_pb, data_pb = _assert_playbook_contains(
                        case_id=case_id, expected=expected_for_assert, resp=create_resp
                    )
                    playbook_ok = play_ok
                    log["stageB_assert"] = {"ok": play_ok, "notes": notes_pb}
                    # Store playbook only in success or limited size.
                    if play_ok:
                        playbook = data_pb.get("playbook") or ""
                        log["playbook_snippet"] = playbook[:4000]

    log["finished_at"] = datetime.utcnow().isoformat()
    log["result_ok"] = bool(ok_preview and playbook_ok)
    return log


def main() -> int:
    _load_dotenv_if_present()

    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.getenv("INTENT_REGRESSION_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--provider", default=os.getenv("INTENT_REGRESSION_PROVIDER", "ollama"))
    ap.add_argument("--model", default=os.getenv("INTENT_REGRESSION_MODEL", "qwen2.5-coder:3b"))
    ap.add_argument("--ollama-base-url", default=os.getenv("INTENT_REGRESSION_OLLAMA_BASE_URL", None))
    ap.add_argument("--timeout-s", type=int, default=int(os.getenv("INTENT_REGRESSION_TIMEOUT_S", "120")))
    ap.add_argument("--log-dir", default=os.getenv("INTENT_REGRESSION_LOG_DIR", "intent_regression_logs"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--verify-with-dry-run",
        action="store_true",
        help="Run Ansible in check-mode during create-and-plan validation (slow).",
    )
    args = ap.parse_args()

    log_dir = Path(args.log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    cfg = RunnerConfig(
        base_url=str(args.base_url),
        provider=str(args.provider),
        model=str(args.model),
        ollama_base_url=args.ollama_base_url,
        timeout_s=int(args.timeout_s),
        log_dir=log_dir,
        dry_run=bool(args.dry_run),
        max_disambiguation_rounds=3,
        verify_with_dry_run=bool(args.verify_with_dry_run),
    )

    if not CASES_PATH.is_file():
        print(f"Missing cases file: {CASES_PATH}", file=sys.stderr)
        return 2
    if not GOLDEN_PATH.is_file():
        print(f"Missing golden file: {GOLDEN_PATH}", file=sys.stderr)
        return 2

    cases_doc = _load_yaml(CASES_PATH)
    golden_doc = _load_yaml(GOLDEN_PATH)

    cases: List[Dict[str, Any]] = cases_doc.get("cases") or []
    if not cases:
        print("No cases found in intent_prompt_cases.yaml", file=sys.stderr)
        return 2

    all_logs: List[Dict[str, Any]] = []
    for idx, case in enumerate(cases, start=1):
        print(f"[{idx}/{len(cases)}] Running case: {case.get('id')}")
        log = run_case(case=case, golden=golden_doc, cfg=cfg)
        all_logs.append(log)
        # Persist per-case logs.
        out_path = cfg.log_dir / f"{log['case_id']}.json"
        out_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        if not log.get("result_ok"):
            print(f"  -> FAIL: case_id={log.get('case_id')}")
        else:
            print("  -> OK")

    # Summary
    ok_count = sum(1 for l in all_logs if l.get("result_ok"))
    fail_count = len(all_logs) - ok_count
    summary = {
        "ran_at": _now_iso(),
        "total": len(all_logs),
        "ok": ok_count,
        "fail": fail_count,
        "base_url": cfg.base_url,
        "provider": cfg.provider,
        "model": cfg.model,
        "dry_run": cfg.dry_run,
    }
    (cfg.log_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Finished. total={len(all_logs)} ok={ok_count} fail={fail_count}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

