"""
Preview-intent report + docs updater for docs/test_case/operation_by_category.md.

This script:
  1) Loads prompt cases from backend/ansible/intent_prompt_cases.yaml
  2) Selects only the 42 checklist items that correspond to docs/test_case/operation_by_category.md
     (we exclude derived negatives whose case_id contains "missing_")
  3) Calls POST /api/v1/ansible/preview-intent for each prompt
  4) Writes a compact summary into the "**Current Behavior:** (paste output here)" slots
     in docs/test_case/operation_by_category.md

Safety: preview-intent does NOT execute Ansible playbooks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

import requests
import yaml


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _is_checklist_case(case: Dict[str, Any]) -> bool:
    cid = str(case.get("id") or "")
    # We want the 42 items that correspond to docs/test_case/operation_by_category.md.
    # Derived negative cases use ids like:
    #   vlan_create_missing_..., iface_config_*_missing_..., acl_create_missing_..., qos_create_missing_...
    # but the Ambiguity section in docs also contains the word "missing" in ids (they start with `amb_`).
    derived_prefixes = (
        "vlan_create_missing_",
        "vlan_trunk_missing_",
        "iface_config_access_missing_",
        "iface_config_trunk_missing_",
        "iface_config_routed_missing_",
        "acl_create_missing_",
        "qos_create_missing_",
        "static_route_missing_",
        "ospf_missing_process_id_",
        "ospf_missing_networks_",
    )
    if cid.startswith("amb_"):
        return True
    return not cid.startswith(derived_prefixes)


def _extract_fields_from_params_disambiguation(pdis: Any) -> List[str]:
    if not isinstance(pdis, dict):
        return []
    fields = pdis.get("fields")
    if not isinstance(fields, list):
        return []
    out: List[str] = []
    for f in fields:
        if isinstance(f, dict) and f.get("field"):
            out.append(str(f["field"]))
    return out


def _safe_trunc(s: str, limit: int = 140) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _extract_preview_summary(resp_json: Dict[str, Any]) -> str:
    # refs are already “template selection” signal
    refs = resp_json.get("references") or []
    ref_paths: List[str] = []
    if isinstance(refs, list):
        for r in refs[:2]:
            if isinstance(r, dict) and r.get("path"):
                ref_paths.append(str(r["path"]))
    parsed = resp_json.get("parsed_intent") or {}
    resolved_devices = parsed.get("resolved_devices") if isinstance(parsed, dict) else None
    if not isinstance(resolved_devices, list):
        resolved_devices = []

    pdis = resp_json.get("params_disambiguation")
    pdis_fields = _extract_fields_from_params_disambiguation(pdis)

    devices_dis = resp_json.get("devices_disambiguation") or {}
    ask_device = isinstance(devices_dis, dict) and bool(devices_dis.get("ask_user_selection"))

    parts: List[str] = []
    if resolved_devices:
        parts.append("devices=" + ",".join(str(d) for d in resolved_devices[:4]))
    if ref_paths:
        parts.append("refs=" + ",".join(ref_paths))
    if pdis_fields:
        parts.append("ask_fields=" + ",".join(pdis_fields))
    if ask_device:
        parts.append("ask_devices")

    if not parts:
        return "LLM parsed (no disambiguation / refs in response)"
    return _safe_trunc("; ".join(parts), 160)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--provider", default="ollama")
    ap.add_argument("--model", default="qwen2.5-coder:3b")
    ap.add_argument("--ollama-base-url", default="http://192.168.1.251:11434")
    ap.add_argument("--timeout-s", type=int, default=180)
    ap.add_argument("--retries", type=int, default=2)
    args = ap.parse_args()

    # repo_root = <workspace>/ (two levels up from backend/ansible/)
    repo_root = Path(__file__).resolve().parents[2]
    cases_path = repo_root / "backend" / "ansible" / "intent_prompt_cases.yaml"
    md_path = repo_root / "docs" / "test_case" / "operation_by_category.md"

    cases_doc = _load_yaml(cases_path)
    cases: List[Dict[str, Any]] = cases_doc.get("cases") or []
    cases = [c for c in cases if _is_checklist_case(c)]

    if not md_path.exists():
        raise SystemExit(f"Missing markdown: {md_path}")

    md = md_path.read_text(encoding="utf-8")

    # Replace occurrences sequentially; each checklist item has one placeholder.
    placeholder_count = md.count("(paste output here)")
    if placeholder_count != len(cases):
        print(
            f"WARNING: placeholder count ({placeholder_count}) != case count ({len(cases)}). "
            f"Proceeding with min count.",
            flush=True,
        )

    n = min(placeholder_count, len(cases))
    url = args.base_url.rstrip("/") + "/api/v1/ansible/preview-intent"

    out_items: List[Dict[str, Any]] = []
    for i in range(n):
        c = cases[i]
        cid = str(c.get("id") or "")
        prompt = str(c.get("intent_description") or "")
        feature = str(c.get("category") or "")
        payload: Dict[str, Any] = {
            "intent_description": prompt,
            "feature": feature,
            "vendor": "",
            "target_hosts": [],
            "provider": args.provider,
            "model": args.model,
            "ollama_base_url": args.ollama_base_url,
            # leave params empty; preview computes disambiguation
        }
        r = None
        last_err: str | None = None
        for attempt in range(args.retries + 1):
            try:
                r = requests.post(url, json=payload, timeout=args.timeout_s)
                last_err = None
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
        if r is None:
            preview_summary = _safe_trunc(f"Preview request failed: {last_err}", 170)
            preview_json = {}
        elif r.status_code != 200:
            preview_summary = f"HTTP {r.status_code}: {_safe_trunc(r.text, 80)}"
            preview_json = {}
        else:
            preview_json = r.json() if r.content else {}
            preview_summary = _extract_preview_summary(preview_json)

        out_items.append({"case_id": cid, "summary": preview_summary})

        # Replace the first remaining occurrence so indices never drift.
        if "(paste output here)" not in md:
            break
        md = md.replace("(paste output here)", f"({preview_summary})", 1)
        md_path.write_text(md, encoding="utf-8")
        (md_path.parent / "operation_by_category_preview_report.json").write_text(
            json.dumps({"count": i + 1, "items": out_items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"[{i+1}/{n}] {cid}: {preview_summary}", flush=True)

    # final write
    md_path.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

