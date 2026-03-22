"""
Central loaders for Intent Parsing Tree artifacts:

- intent_graph.yml       — categories → operations (authoritative list; schemas in intent_schemas/*.yml)
- intent_keywords.yml    — editable classification / heuristic keywords
- category_schemas/*.yml — per-category parameter vocabulary (documentation for LLM; not required/optional)

See docs/intent_operation_reference.md (Source of truth).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ansible.intent_artifacts")

_BASE = Path(__file__).resolve().parent
_INTENT_GRAPH_PATH = _BASE / "intent_graph.yml"
_INTENT_KEYWORDS_PATH = _BASE / "intent_keywords.yml"
_CATEGORY_SCHEMAS_DIR = _BASE / "category_schemas"

_graph_cache: Optional[Dict[str, Any]] = None
_keywords_cache: Optional[Dict[str, Any]] = None


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("Failed to load YAML %s: %s", path, exc)
        return {}


def load_intent_graph(force_reload: bool = False) -> Dict[str, Any]:
    """Load intent_graph.yml (cached)."""
    global _graph_cache
    if _graph_cache is not None and not force_reload:
        return _graph_cache
    data = _load_yaml(_INTENT_GRAPH_PATH)
    _graph_cache = data if isinstance(data, dict) else {}
    return _graph_cache


def load_intent_keywords(force_reload: bool = False) -> Dict[str, Any]:
    """Load intent_keywords.yml (cached)."""
    global _keywords_cache
    if _keywords_cache is not None and not force_reload:
        return _keywords_cache
    data = _load_yaml(_INTENT_KEYWORDS_PATH)
    _keywords_cache = data if isinstance(data, dict) else {}
    return _keywords_cache


def invalidate_caches() -> None:
    global _graph_cache, _keywords_cache
    _graph_cache = None
    _keywords_cache = None


def get_role_aliases() -> Dict[str, str]:
    kw = load_intent_keywords()
    raw = kw.get("role_aliases") or {}
    if isinstance(raw, dict):
        return {str(k).strip().lower().replace(" ", "_"): str(v).strip() for k, v in raw.items() if k}
    return {}


def get_vendor_aliases() -> Dict[str, str]:
    kw = load_intent_keywords()
    raw = kw.get("vendor_aliases") or {}
    if isinstance(raw, dict):
        return {str(k).strip().lower().replace(" ", "_"): str(v).strip() for k, v in raw.items() if k}
    return {}


def get_query_rule_based_patterns() -> List[str]:
    kw = load_intent_keywords()
    p = kw.get("query_rule_based_patterns") or []
    return [str(x).strip().lower() for x in p if str(x).strip()]


def get_audit_keywords() -> List[str]:
    kw = load_intent_keywords()
    p = kw.get("audit_keywords") or []
    return [str(x).strip().lower() for x in p if str(x).strip()]


def validate_intent_graph_against_schemas() -> List[str]:
    """
    Ensure every operation listed under intent_graph.yml categories has a matching
    intent_schemas/<action>.yml. Returns human-readable error strings (empty = OK).
    """
    from ansible.intent_validation import load_schema

    errors: List[str] = []
    graph = load_intent_graph()
    categories = graph.get("categories") or {}
    if not isinstance(categories, dict):
        return ["intent_graph: 'categories' must be a mapping"]
    for cat_key, node in categories.items():
        if not isinstance(node, dict):
            errors.append(f"Category {cat_key!r}: expected mapping")
            continue
        ops = node.get("operations") or []
        if not isinstance(ops, list):
            errors.append(f"Category {cat_key!r}: 'operations' must be a list")
            continue
        for op in ops:
            name = str(op or "").strip()
            if not name:
                continue
            if not load_schema(name):
                errors.append(
                    f"Category {cat_key!r}: operation {name!r} has no intent_schemas/{name}.yml"
                )
    return errors


def get_category_vocabulary_for_prompt(max_chars: int = 14000) -> str:
    """
    Concatenate category_schemas/*.yml into a compact markdown block for the LLM parser.
    """
    if not _CATEGORY_SCHEMAS_DIR.is_dir():
        return "(no category_schemas directory)"
    parts: List[str] = []
    for path in sorted(_CATEGORY_SCHEMAS_DIR.glob("*.yml")):
        doc = _load_yaml(path)
        if not doc:
            continue
        cat = str(doc.get("category") or path.stem)
        title = str(doc.get("display_name") or cat)
        desc = str(doc.get("description") or "").strip()
        lines = [f"### {title} (`{cat}`)", desc]
        vocab = doc.get("parameter_vocabulary") or []
        if isinstance(vocab, list):
            for item in vocab:
                if not isinstance(item, dict):
                    continue
                n = str(item.get("name") or "").strip()
                d = str(item.get("description") or "").strip()
                if n:
                    lines.append(f"  - **{n}**: {d or '—'}")
        parts.append("\n".join(lines))
    text = "\n\n".join(parts) if parts else "(empty category_schemas)"
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n… (truncated)"
    return text


def get_intent_graph_summary_for_prompt(max_chars: int = 4000) -> str:
    """Short summary: categories and their operations (from intent_graph.yml)."""
    graph = load_intent_graph()
    root = graph.get("root") or {}
    lines = [
        "## Intent graph (categories → operations)",
        f"Root: **{root.get('id', 'target_devices')}** — {str(root.get('description') or '').strip()}",
        "",
        "Per-operation required/optional fields are **only** in `intent_schemas/<action>.yml`.",
        "",
    ]
    categories = graph.get("categories") or {}
    if isinstance(categories, dict):
        for ck in sorted(categories.keys()):
            node = categories[ck]
            if not isinstance(node, dict):
                continue
            feat = str(node.get("feature") or ck)
            ops = node.get("operations") or []
            op_str = ", ".join(str(o) for o in ops) if isinstance(ops, list) else ""
            desc = str(node.get("description") or "").strip().replace("\n", " ")
            lines.append(f"- **{ck}** (feature `{feat}`): operations [{op_str}]. {desc}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n… (truncated)"
    return text


def get_intent_keywords_digest_for_prompt(max_chars: int = 6000) -> str:
    """
    Editable heuristics surfaced to the LLM: query/audit triggers, broad-scope device phrases.
    """
    kw = load_intent_keywords()
    lines = [
        "## Keyword hints (rule-based classifier mirrors these)",
        "",
        "**Query-like messages** (substring match, lowercased):",
    ]
    for p in get_query_rule_based_patterns()[:40]:
        lines.append(f"  - `{p}`")
    lines.append("")
    lines.append("**Audit-like keywords:**")
    for p in get_audit_keywords():
        lines.append(f"  - `{p}`")
    lines.append("")
    bds = kw.get("broad_device_scope") or {}
    if isinstance(bds, dict):
        phrases = bds.get("literal_phrases") or []
        lines.append("**Broad device scope** (user explicitly asked wide target — do not treat as ambiguous):")
        for ph in phrases:
            lines.append(f"  - phrase: `{ph}`")
        rx = bds.get("regex_patterns") or []
        for r in rx:
            lines.append(f"  - regex: `{r}`")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n… (truncated)"
    return text


def intent_explicitly_requests_broad_device_scope(intent_description: str) -> bool:
    """Data-driven equivalent of former intent_tree_engine heuristic."""
    t = re.sub(r"\s+", " ", (intent_description or "").strip().lower())
    if not t:
        return False
    kw = load_intent_keywords()
    bds = kw.get("broad_device_scope") or {}
    if isinstance(bds, dict):
        for ph in bds.get("literal_phrases") or []:
            if str(ph).lower() in t:
                return True
        for pattern in bds.get("regex_patterns") or []:
            try:
                if re.search(str(pattern), t, re.IGNORECASE):
                    return True
            except re.error:
                continue
    return False


def message_matches_create_vlan_imperative_fast_path(msg: str) -> bool:
    """Data-driven create-VLAN fast path gate (was hardcoded in llm/intent.py)."""
    raw = (msg or "").strip()
    if not raw:
        return False
    tl = re.sub(r"\s+", " ", raw.lower())
    kw = load_intent_keywords()
    cv = kw.get("create_vlan_fast_path") or {}
    if not isinstance(cv, dict):
        cv = {}
    for prefix in cv.get("query_message_prefixes") or []:
        p = str(prefix).strip().lower()
        if p and tl.startswith(p + " "):
            return False
    must = str(cv.get("regex_must_match") or r"\bcreate\s+vlan\b")
    try:
        if not re.search(must, tl, re.IGNORECASE):
            return False
    except re.error:
        if not re.search(r"\bcreate\s+vlan\b", tl, re.IGNORECASE):
            return False
    ex_mt = str(cv.get("regex_exclude_multi_task") or "")
    if ex_mt:
        try:
            if re.search(ex_mt, tl, re.IGNORECASE):
                return False
        except re.error:
            pass
    pb = str(cv.get("regex_exclude_port_binding") or "")
    if pb and " and " in tl:
        try:
            if re.search(pb, tl, re.IGNORECASE):
                return False
        except re.error:
            pass
    return True


def list_graph_operations_flat() -> List[str]:
    """All operation action names declared in intent_graph.yml."""
    out: List[str] = []
    graph = load_intent_graph()
    categories = graph.get("categories") or {}
    if not isinstance(categories, dict):
        return out
    for node in categories.values():
        if not isinstance(node, dict):
            continue
        for op in node.get("operations") or []:
            s = str(op or "").strip()
            if s and s not in out:
                out.append(s)
    return sorted(out)
