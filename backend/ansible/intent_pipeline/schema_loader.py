"""
Load intent_catalog YAML and expose category detection + operation summaries.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

CATALOG_ROOT = Path(__file__).resolve().parent.parent / "intent_catalog"
CATEGORIES_DIR = CATALOG_ROOT / "categories"


class IntentCatalog:
    """In-memory catalog built from YAML files."""

    def __init__(self) -> None:
        self.global_fields: Dict[str, Any] = {}
        self.keyword_map: Dict[str, Any] = {}
        self.priority_order: List[str] = []
        self.categories: Dict[str, Dict[str, Any]] = {}

    def detect_categories(self, user_message: str) -> List[str]:
        """Return ordered list of category keys (aliases, operation_hints, op triggers)."""
        from ansible.intent_pipeline.keyword_manager import detect_categories_for_message

        return detect_categories_for_message(user_message, self)

    def find_operation(self, action: str, feature: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return (operation_key, spec) when action/feature match a declared operation."""
        return find_operation_spec_in_catalog(self, action, feature)

    def format_operations_block(self, category_keys: Optional[List[str]] = None) -> str:
        """Human-readable operations + rules for prompt injection."""
        keys = category_keys or list(self.categories.keys())
        lines: List[str] = []
        for ck in keys:
            cat = self.categories.get(ck)
            if not cat:
                continue
            disp = cat.get("display_name", ck)
            lines.append(f"### {disp} (`{ck}`)")
            ops = cat.get("operations") or {}
            for op_key, spec in ops.items():
                action = spec.get("action", op_key)
                feat = spec.get("feature", "")
                req = spec.get("required_parameters") or []
                opt = spec.get("optional_parameters") or []
                ban = spec.get("forbidden_fields") or []
                dep = spec.get("dependencies") or []
                groups = spec.get("required_parameter_groups") or {}
                any_of = groups.get("any_of") if isinstance(groups, dict) else None
                rules = spec.get("rules") or []
                lines.append(f"- **{op_key}** → action `{action}`, feature `{feat}`")
                lines.append(f"  - required parameters: {', '.join(req) if req else '(none)'}")
                if isinstance(any_of, list) and any_of:
                    lines.append(
                        "  - required (any of): satisfy **one** group fully: "
                        + " | ".join(f"[{', '.join(g)}]" if isinstance(g, list) else str(g) for g in any_of)
                    )
                if opt:
                    lines.append(f"  - optional parameters: {', '.join(str(x) for x in opt)}")
                if ban:
                    lines.append(f"  - forbidden fields (must not appear): {', '.join(str(x) for x in ban)}")
                if dep:
                    lines.append(f"  - dependencies / cross-checks: {'; '.join(str(x) for x in dep)}")
                lines.append("  - any other parameter names are optional unless stated in rules or forbidden above")
                for r in rules:
                    lines.append(f"  - {r}")
            lines.append("")
        return "\n".join(lines).strip() or "(no operations)"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing intent catalog file: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root (expected mapping): {path}")
    return data


def find_operation_spec_by_operation_key(
    catalog: IntentCatalog,
    operation_key: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return (operation_key, spec) when ``categories.*.operations`` contains the key."""
    key = str(operation_key or "").strip()
    if not key:
        return None
    for _ck, cat in catalog.categories.items():
        ops = cat.get("operations") or {}
        spec = ops.get(key)
        if isinstance(spec, dict):
            return key, spec
    return None


def find_operation_spec_in_catalog(
    catalog: IntentCatalog,
    action: str,
    feature: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Locate operation spec by matching action/feature against category YAML operations."""
    act = str(action or "").strip()
    feat = str(feature or "").strip()
    for _cat_key, cat in catalog.categories.items():
        ops = cat.get("operations") or {}
        for op_name, spec in ops.items():
            if not isinstance(spec, dict):
                continue
            if spec.get("action") == act and (not feat or spec.get("feature") == feat):
                return str(op_name), spec
            if str(op_name) == act:
                return str(op_name), spec
    return None


def load_intent_catalog() -> IntentCatalog:
    catalog = IntentCatalog()
    gf = _load_yaml(CATALOG_ROOT / "global_fields.yaml")
    catalog.global_fields = gf.get("global_fields") or {}

    km = _load_yaml(CATALOG_ROOT / "keyword_map.yaml")
    catalog.keyword_map = km
    catalog.priority_order = list(km.get("priority_order") or [])

    if not CATEGORIES_DIR.is_dir():
        raise FileNotFoundError(f"Missing categories dir: {CATEGORIES_DIR}")

    for path in sorted(CATEGORIES_DIR.glob("*.yaml")):
        raw = _load_yaml(path)
        key = raw.get("category_key") or path.stem
        catalog.categories[key] = raw

    return catalog


_cached: Optional[IntentCatalog] = None


def get_catalog() -> IntentCatalog:
    global _cached
    if _cached is None:
        _cached = load_intent_catalog()
    return _cached


def reload_catalog() -> IntentCatalog:
    global _cached
    _cached = load_intent_catalog()
    return _cached
