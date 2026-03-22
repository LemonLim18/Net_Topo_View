"""
Serialize / deserialize intent category YAML for the admin UI (no optional_parameters; no forbidden).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from ansible.intent_pipeline.schema_loader import CATEGORIES_DIR

DATA_TYPES = frozenset({"string", "integer", "enum", "array", "object", "boolean"})


def category_yaml_path(category_key: str) -> Path:
    safe = (category_key or "").strip().replace("\\", "/").split("/")[-1]
    if not safe or ".." in safe:
        raise ValueError("Invalid category key")
    return CATEGORIES_DIR / f"{safe}.yaml"


def load_category_ui_dict(category_key: str) -> Dict[str, Any]:
    path = category_yaml_path(category_key)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError("Invalid category YAML root")
    return normalize_category_dict(raw, category_key)


def normalize_category_dict(raw: Dict[str, Any], category_key: str) -> Dict[str, Any]:
    """Strip legacy optional/forbidden; add defaults for UI."""
    key = str(raw.get("category_key") or category_key).strip()
    ops_in = raw.get("operations") or {}
    operations_out: List[Dict[str, Any]] = []
    if isinstance(ops_in, dict):
        for oname, spec in ops_in.items():
            if not isinstance(spec, dict):
                continue
            operations_out.append(
                {
                    "name": str(oname),
                    "action": str(spec.get("action") or oname),
                    "feature": str(spec.get("feature") or ""),
                    "required_parameters": list(spec.get("required_parameters") or []),
                    "implicit_requires_targets": bool(spec.get("implicit_requires_targets", False)),
                    "rules": [str(r) for r in (spec.get("rules") or []) if str(r).strip()],
                }
            )
    shared = raw.get("shared_fields") or []
    shared_out: List[Dict[str, Any]] = []
    if isinstance(shared, list):
        for row in shared:
            if not isinstance(row, dict):
                continue
            nm = str(row.get("name") or "").strip()
            if not nm:
                continue
            dt = str(row.get("data_type") or "string").lower()
            if dt not in DATA_TYPES:
                dt = "string"
            shared_out.append(
                {
                    "name": nm,
                    "data_type": dt,
                    "required": bool(row.get("required", False)),
                }
            )
    return {
        "category_key": key,
        "display_name": str(raw.get("display_name") or key),
        "category_description": str(raw.get("category_description") or ""),
        "legacy_feature": str(raw.get("legacy_feature") or key),
        "shared_fields": shared_out,
        "operations": operations_out,
    }


def build_category_yaml_document(ui: Dict[str, Any]) -> Dict[str, Any]:
    """Build on-disk YAML mapping (no optional_parameters / forbidden)."""
    ck = str(ui.get("category_key") or "").strip()
    if not ck:
        raise ValueError("category_key required")
    doc: Dict[str, Any] = {
        "category_key": ck,
        "display_name": str(ui.get("display_name") or ck),
        "category_description": str(ui.get("category_description") or ""),
        "legacy_feature": str(ui.get("legacy_feature") or ck),
        "shared_fields": [],
        "operations": {},
    }
    for row in ui.get("shared_fields") or []:
        if not isinstance(row, dict):
            continue
        nm = str(row.get("name") or "").strip()
        if not nm:
            continue
        dt = str(row.get("data_type") or "string").lower()
        if dt not in DATA_TYPES:
            dt = "string"
        doc["shared_fields"].append(
            {"name": nm, "data_type": dt, "required": bool(row.get("required", False))}
        )
    ops = ui.get("operations") or []
    if isinstance(ops, list):
        for spec in ops:
            if not isinstance(spec, dict):
                continue
            oname = str(spec.get("name") or "").strip()
            if not oname:
                continue
            req = spec.get("required_parameters") or []
            if not isinstance(req, list):
                req = []
            req_clean = [str(x).strip() for x in req if str(x).strip()]
            rules = spec.get("rules") or []
            rules_clean = [str(r).strip() for r in rules if isinstance(r, str) and str(r).strip()]
            op_doc: Dict[str, Any] = {
                "action": str(spec.get("action") or oname),
                "feature": str(spec.get("feature") or ""),
                "required_parameters": req_clean,
                "rules": rules_clean,
            }
            if spec.get("implicit_requires_targets"):
                op_doc["implicit_requires_targets"] = True
            doc["operations"][oname] = op_doc
    return doc


def dump_category_yaml(doc: Dict[str, Any]) -> str:
    return yaml.dump(
        doc,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    )
