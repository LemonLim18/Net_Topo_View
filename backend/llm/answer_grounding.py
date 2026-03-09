from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional


_PLANNED_SCOPES = {"planned_inventory", "planned_topology"}
_LIVE_SCOPES = {
    "live_topology",
    "telemetry",
    "config_history",
    "incidents",
    "drift",
    "security_alerts",
    "incident_state",
    "drift_state",
    "device_configs",
    "change_management",
    "deployment_status",
    "sdn_state",
}
_INFERRED_SCOPES = {"knowledge_documents"}


def _decode_tool_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"message": raw}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": raw}


def _iter_source_metadata(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    keys = ("source_metadata", "inventory_source_metadata", "live_source_metadata")
    for key in keys:
        metadata = payload.get(key)
        if not isinstance(metadata, dict):
            continue
        if "source" in metadata and "scope" in metadata:
            yield metadata
            continue
        for value in metadata.values():
            if isinstance(value, dict) and "source" in value and "scope" in value:
                yield value


def _normalize_source_entry(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": str(metadata.get("source") or "unknown"),
        "scope": str(metadata.get("scope") or "unknown"),
        "captured_at": metadata.get("captured_at"),
        "freshness_class": str(metadata.get("freshness_class") or "unknown"),
        "age_seconds": metadata.get("age_seconds"),
        "confidence": float(metadata.get("confidence") or 0.0),
    }


def _dedupe_source_entries(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for entry in entries:
        key = (entry.get("source"), entry.get("scope"), entry.get("captured_at"), entry.get("age_seconds"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _answer_scope_from_sources(query_class: str, source_age: List[Dict[str, Any]]) -> str:
    scopes = {str(entry.get("scope") or "unknown") for entry in source_age}
    has_planned = bool(scopes & _PLANNED_SCOPES)
    has_live = bool(scopes & _LIVE_SCOPES)
    has_inferred = bool(scopes & _INFERRED_SCOPES)

    if query_class == "planned_vs_live" or (has_planned and has_live):
        return "mixed"
    if has_planned and not has_live and not has_inferred:
        return "planned"
    if has_live and not has_planned and not has_inferred:
        return "live"
    if has_inferred and not has_planned and not has_live:
        return "inferred"
    if has_planned or has_live:
        return "mixed" if has_inferred else ("planned" if has_planned else "live")

    if query_class == "inventory_only":
        return "planned"
    if query_class in {"live_only", "troubleshooting"}:
        return "live"
    if query_class == "knowledge_heavy":
        return "inferred"
    return "inferred"


def _confidence_score(
    source_age: List[Dict[str, Any]],
    answer_scope: str,
    tool_names: List[str],
    errors: List[str],
) -> float:
    if not source_age:
        base = 0.32
    else:
        avg_confidence = sum(entry.get("confidence", 0.0) for entry in source_age) / len(source_age)
        freshness_bonus = 0.0
        for entry in source_age:
            freshness = entry.get("freshness_class")
            if freshness == "live":
                freshness_bonus += 0.08
            elif freshness == "warm":
                freshness_bonus += 0.03
            elif freshness == "stale":
                freshness_bonus -= 0.08
            elif freshness == "missing":
                freshness_bonus -= 0.14
        freshness_bonus /= max(len(source_age), 1)
        base = avg_confidence + freshness_bonus

    if len(tool_names) >= 2:
        base += 0.08
    if answer_scope == "mixed" and any(name == "compare_planned_vs_live" for name in tool_names):
        base += 0.08
    if answer_scope == "inferred":
        base -= 0.12
    if errors:
        base -= min(0.25, 0.08 * len(errors))

    return max(0.05, min(0.99, round(base, 2)))


def _confidence_level(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def build_answer_grounding(
    query_class: str,
    tool_results: Dict[str, Any],
    knowledge_used: bool = False,
) -> Dict[str, Any]:
    tool_names = list(tool_results.keys())
    payloads = [_decode_tool_payload(raw) for raw in tool_results.values()]
    errors = [payload.get("error") for payload in payloads if payload.get("error")]

    source_entries: List[Dict[str, Any]] = []
    source_used: List[str] = []
    for payload in payloads:
        raw_source_used = payload.get("source_used")
        if isinstance(raw_source_used, str):
            source_used.append(raw_source_used)
        elif isinstance(raw_source_used, list):
            source_used.extend(str(item) for item in raw_source_used if item)

        for metadata in _iter_source_metadata(payload):
            normalized = _normalize_source_entry(metadata)
            source_entries.append(normalized)
            source_used.append(normalized["source"])

    if knowledge_used and "vector_store" not in source_used:
        source_used.append("vector_store")
        source_entries.append(
            {
                "source": "vector_store",
                "scope": "knowledge_documents",
                "captured_at": None,
                "freshness_class": "warm",
                "age_seconds": None,
                "confidence": 0.7,
            }
        )

    source_age = _dedupe_source_entries(source_entries)
    deduped_sources: List[str] = []
    seen_sources = set()
    for source in source_used:
        if source in seen_sources:
            continue
        seen_sources.add(source)
        deduped_sources.append(source)

    answer_scope = _answer_scope_from_sources(query_class, source_age)
    confidence_score = _confidence_score(source_age, answer_scope, tool_names, errors)
    confidence_level = _confidence_level(confidence_score)

    if source_age:
        reason = f"Computed from {len(source_age)} authoritative evidence snapshot(s)"
    else:
        reason = "No authoritative tool evidence was captured for this turn; answer is grounded only by prompt context"

    if errors:
        reason = f"{reason}; degraded by tool errors"

    return {
        "query_class": query_class,
        "answer_scope": answer_scope,
        "source_used": deduped_sources,
        "source_age": source_age,
        "tool_evidence": tool_names,
        "confidence": {
            "level": confidence_level,
            "score": confidence_score,
            "reason": reason,
        },
    }


def format_grounding_summary(grounding: Dict[str, Any]) -> str:
    answer_scope = grounding.get("answer_scope", "inferred")
    sources = grounding.get("source_used") or []
    source_text = ", ".join(str(source) for source in sources) if sources else "prompt_context_only"

    age_parts = []
    for entry in grounding.get("source_age") or []:
        source = entry.get("source", "unknown")
        age_seconds = entry.get("age_seconds")
        freshness = entry.get("freshness_class", "unknown")
        if age_seconds is None:
            age_parts.append(f"{source}:unknown/{freshness}")
        else:
            age_parts.append(f"{source}:{age_seconds}s/{freshness}")
    age_text = ", ".join(age_parts) if age_parts else "unknown"

    confidence = grounding.get("confidence") or {}
    level = confidence.get("level", "low")
    score = confidence.get("score", 0.0)
    return (
        f"Grounding: {answer_scope}; sources={source_text}; "
        f"source_age={age_text}; confidence={level} ({score})."
    )