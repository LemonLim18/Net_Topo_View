"""
Dry-run reporter for docs/test_case/operation_by_category.md prompts.

For each checklist prompt (42 total), calls:
  POST /api/v1/ansible/create-and-plan
with verify_with_dry_run=true so the backend runs Ansible in check-mode only.

Output is printed to stdout as a JSON object:
  { "items": [ { "case_id": ..., "prompt": ..., "status": "ok|error|skipped", "message": ... }, ... ] }

This script does not modify files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

import requests
import yaml


def _load_cases(path: str) -> List[Dict[str, Any]]:
    doc = yaml.safe_load(open(path, "r", encoding="utf-8").read())
    return doc.get("cases") or []


def _is_checklist_case(case: Dict[str, Any]) -> bool:
    # Derived/negative cases all contain "missing_" in their id in our current corpus.
    cid = str(case.get("id") or "")
    return "missing_" not in cid


def _shorten(s: str, limit: int = 160) -> str:
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[:limit - 1] + "…"


def _extract_detail_message(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        if "message" in detail and isinstance(detail["message"], str):
            return detail["message"]
        # Sometimes detail has {errors: [...]}
        for k in ("errors", "validation_errors", "validation_issues"):
            v = detail.get(k)
            if isinstance(v, list) and v:
                return _shorten(str(v[0]))
        return _shorten(json.dumps(detail, ensure_ascii=False))
    return _shorten(str(detail))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--ollama-base-url", default=None)
    ap.add_argument("--provider", default="ollama")
    ap.add_argument("--model", default="qwen2.5-coder:3b")
    ap.add_argument("--timeout-s", type=int, default=180)
    args = ap.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cases_path = os.path.join(repo_root, "ansible", "intent_prompt_cases.yaml")
    cases = _load_cases(cases_path)
    cases = [c for c in cases if _is_checklist_case(c)]
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    # We expect exactly 42 checklist cases from operation_by_category.md.
    # If not, still run what we have but report count.
    create_url = args.base_url.rstrip("/") + "/api/v1/ansible/create-and-plan"

    items: List[Dict[str, Any]] = []
    for idx, c in enumerate(cases, start=1):
        cid = str(c.get("id") or "")
        prompt = str(c.get("intent_description") or "")
        feature = str(c.get("category") or "")

        payload: Dict[str, Any] = {
            "intent_description": prompt,
            "vendor": "",
            "feature": feature,
            "params": {},
            "device_id": 0,
            "provider": args.provider,
            "model": args.model,
            "verify_with_dry_run": True,
            # Speed up: we only need check-mode validation, not high-quality refinement.
            "auto_refine": False,
            "max_refinement_attempts": 1,
        }
        if args.ollama_base_url:
            payload["ollama_base_url"] = args.ollama_base_url

        try:
            r = requests.post(create_url, json=payload, timeout=args.timeout_s)
        except Exception as e:
            items.append(
                {
                    "case_id": cid,
                    "prompt": prompt,
                    "status": "error",
                    "message": _shorten(f"Request failed: {e}"),
                }
            )
            continue

        if r.status_code == 200:
            # If verify_with_dry_run=true, the backend treats dry-run failures as validation issues
            # and escalates to HTTP 422 rather than returning playbook.
            items.append(
                {
                    "case_id": cid,
                    "prompt": prompt,
                    "status": "ok",
                    "message": "Dry-run PASSED (playbook_generated)",
                }
            )
        else:
            msg = "Dry-run FAILED"
            try:
                j = r.json()
                detail = j.get("detail")
                msg = _shorten(_extract_detail_message(detail), limit=220)
            except Exception:
                msg = _shorten(r.text or "Dry-run FAILED", limit=220)
            items.append(
                {
                    "case_id": cid,
                    "prompt": prompt,
                    "status": "error",
                    "message": msg,
                }
            )

        # Light progress output to stdout (still machine-readable-ish; main JSON printed at end)
        print(f"[{idx}/{len(cases)}] {cid}: {items[-1]['status']}", flush=True)

    out = {"count": len(cases), "items": items}
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

