"""
Keyword and phrase signals for category / operation routing (data-driven).

Reads:
- ``intent_catalog/keyword_map.yaml`` (categories, priority_order, operation_hints)
- Per-operation ``keyword_triggers`` on category YAML operation specs

No hardcoded category lists in code paths — fallbacks only when YAML is empty.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

logger = logging.getLogger(__name__)

_KEYWORD_MAP_PATH = Path(__file__).resolve().parent.parent / "intent_catalog" / "keyword_map.yaml"


def _load_keyword_map_file() -> Dict[str, Any]:
    if not _KEYWORD_MAP_PATH.is_file():
        return {}
    try:
        raw = yaml.safe_load(_KEYWORD_MAP_PATH.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.warning("keyword_map load failed: %s", exc)
        return {}


def _operation_triggers_from_catalog(catalog: Any) -> Dict[str, List[str]]:
    """operation_key -> trigger phrases from category YAML."""
    out: Dict[str, List[str]] = {}
    for _ck, cat in getattr(catalog, "categories", {}).items():
        ops = (cat or {}).get("operations") or {}
        for op_key, spec in ops.items():
            if not isinstance(spec, dict):
                continue
            triggers = spec.get("keyword_triggers") or spec.get("triggers") or []
            if isinstance(triggers, list) and triggers:
                out.setdefault(str(op_key), []).extend(str(t) for t in triggers if str(t).strip())
    return out


def detect_categories_for_message(user_message: str, catalog: Any) -> List[str]:
    """
    Ordered category keys: priority list first hit, then additional category matches,
    then operation_hint categories, then per-YAML triggers.
    """
    lower = (user_message or "").lower()
    km = getattr(catalog, "keyword_map", None) or _load_keyword_map_file()
    cats = km.get("categories") or {}
    order = list(km.get("priority_order") or []) or list(cats.keys())

    matched: List[str] = []
    for key in order:
        spec = cats.get(key) or {}
        aliases = spec.get("aliases") or []
        for alias in aliases:
            if str(alias).lower() in lower:
                if key not in matched:
                    matched.append(key)
                break

    # operation_hints: { op_or_hint_key: { phrases: [], categories: [] } }
    hints = km.get("operation_hints") or {}
    if isinstance(hints, dict):
        for _hid, hspec in hints.items():
            if not isinstance(hspec, dict):
                continue
            phrases = hspec.get("phrases") or hspec.get("aliases") or []
            cats_hit = hspec.get("categories") or []
            hit = False
            for p in phrases:
                if str(p).lower() in lower:
                    hit = True
                    break
            if hit:
                for ck in cats_hit:
                    ck = str(ck).strip()
                    if ck and ck not in matched:
                        matched.append(ck)

    # Triggers embedded in operation specs
    op_trig = _operation_triggers_from_catalog(catalog)
    for _op, phrases in op_trig.items():
        if any(str(p).lower() in lower for p in phrases):
            # find which category owns this op
            for ck, cat in getattr(catalog, "categories", {}).items():
                if _op in ((cat or {}).get("operations") or {}):
                    if ck not in matched:
                        matched.append(ck)
                    break

    if not matched:
        matched = list(order) or ["vlan", "interface", "acl", "qos", "static_route", "ospf"]
    return matched


def build_operation_hint_block(user_message: str, catalog: Any) -> str:
    """Compact bullet list for LLM prompt: matched phrases → prefer operations."""
    lower = (user_message or "").lower()
    lines: List[str] = []
    km = getattr(catalog, "keyword_map", None) or _load_keyword_map_file()
    hints = km.get("operation_hints") or {}
    if isinstance(hints, dict):
        for hid, hspec in hints.items():
            if not isinstance(hspec, dict):
                continue
            phrases = hspec.get("phrases") or []
            prefers = hspec.get("prefer_operations") or hspec.get("operations") or []
            for p in phrases:
                if str(p).lower() in lower:
                    if prefers:
                        lines.append(
                            f"- Text suggests **{hid}** → prefer operations: {', '.join(str(x) for x in prefers)}"
                        )
                    break

    op_trig = _operation_triggers_from_catalog(catalog)
    for op_key, phrases in op_trig.items():
        if any(str(p).lower() in lower for p in phrases):
            lines.append(f"- Phrase match → consider operation **`{op_key}`**")

    if not lines:
        return ""
    return "## Keyword / operation hints (from catalog)\n" + "\n".join(lines) + "\n"


def merge_keyword_map_into_catalog(catalog: Any) -> None:
    """Ensure catalog.keyword_map is populated (e.g. after partial init)."""
    if not getattr(catalog, "keyword_map", None):
        catalog.keyword_map = _load_keyword_map_file()
