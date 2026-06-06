"""
End-to-end intent pipeline tracing: split → categories → (caller runs LLM) → merge → validate → cross-check → asks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ansible.intent_pipeline.cross_check import cross_check_parsed_intent, merge_cross_check_into_dict
from ansible.intent_pipeline.intent_splitter import segment_summary, split_multi_intent
from ansible.intent_pipeline.interactive_ask import build_interactive_asks
from ansible.intent_pipeline.operation_adapter import apply_operation_adapter, normalize_trunk_access_params
from ansible.intent_pipeline.prompt_builder import detect_categories_for_message
from ansible.intent_pipeline.validation_engine import validate_intent_full


def merge_multi_intent_llm_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    If the model returned ``{{ "intents": [ ... ] }}``, fold into primary + ``_secondary_intents``.
    Also applies operation_adapter per intent when ``canonical_operation`` is set.
    """
    intents = data.get("intents")
    if not isinstance(intents, list) or len(intents) == 0:
        single = dict(data)
        single.pop("intents", None)
        single = apply_operation_adapter(single)
        return normalize_trunk_access_params(single)

    adapted = [normalize_trunk_access_params(apply_operation_adapter(dict(x))) for x in intents if isinstance(x, dict)]
    if not adapted:
        return dict(data)

    primary = adapted[0]
    secondaries: List[Dict[str, Any]] = []
    for item in adapted[1:]:
        secondaries.append(
            {
                "action": item.get("action"),
                "feature": item.get("feature"),
                "parameters": dict(item.get("parameters") or {}),
            }
        )

    params = dict(primary.get("parameters") or {})
    params.pop("_secondary_intents", None)
    primary["parameters"] = params
    if secondaries:
        # ``IntentParser._build_intent`` reads top-level ``secondary_intents`` and copies to parameters.
        primary["secondary_intents"] = secondaries
    for drop in ("intents",):
        primary.pop(drop, None)
    return primary


def prepare_llm_input_dict(raw_after_llm: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize model output (multi-intent + canonical adapter)."""
    return merge_multi_intent_llm_response(raw_after_llm)


def build_pipeline_trace(
    user_message: str,
    parsed_flat: Dict[str, Any],
    db_manager: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Build diagnostic structure for workbench / preview API (no extra LLM cost).

    ``parsed_flat`` should match the JSON shape produced by ``IntentParser`` / ``ParsedIntent.model_dump()``.
    """
    segments = split_multi_intent(user_message)
    cats_per_segment: List[List[str]] = []
    for seg in segments:
        cats, _ = detect_categories_for_message(seg)
        cats_per_segment.append(cats)

    flat_for_val = {
        "action": parsed_flat.get("action"),
        "feature": parsed_flat.get("feature"),
        "parameters": parsed_flat.get("parameters"),
        "scope": parsed_flat.get("scope"),
        "resolved_devices": parsed_flat.get("resolved_devices"),
    }
    vr = validate_intent_full(flat_for_val)
    cross = cross_check_parsed_intent(parsed_flat, db_manager)
    params = dict(parsed_flat.get("parameters") or {})
    cmissing = list(vr.get("missing") or [])
    if not cmissing and params.get("_catalog_missing_fields"):
        cmissing = list(params["_catalog_missing_fields"])

    cand = [str(x) for x in (params.get("_target_device_candidates") or []) if str(x).strip()]
    rd = parsed_flat.get("resolved_devices") or []
    has_resolved_targets = isinstance(rd, list) and any(str(x).strip() for x in rd)
    # User already picked device(s) from UI — do not re-show layer-switch shortlist.
    if has_resolved_targets:
        cand = []
    elif cand:
        cmissing = [f for f in cmissing if f != "target_devices"]

    asks = build_interactive_asks(
        catalog_missing=cmissing,
        cross_check=cross,
        requires_resolution=bool(parsed_flat.get("requires_resolution")),
        catalog_forbidden_violations=list(vr.get("forbidden_violations") or []),
        dependency_notes=list(vr.get("dependency_notes") or []),
        target_device_candidates=cand or None,
        target_device_candidate_reason=str(params.get("_target_device_candidate_reason") or "").strip() or None,
    )

    return {
        "segments": segments,
        "segment_summary": segment_summary(segments),
        "categories_by_segment": cats_per_segment,
        "catalog_validation_ok": bool(vr.get("ok")),
        "catalog_missing_fields": cmissing,
        "catalog_forbidden_violations": list(vr.get("forbidden_violations") or []),
        "catalog_dependency_notes": list(vr.get("dependency_notes") or []),
        "catalog_schema_source": vr.get("schema_source"),
        "catalog_operation_key": vr.get("operation_key"),
        "cross_check": cross,
        "interactive_asks": asks,
    }


def enrich_preview_response(parsed_intent: Dict[str, Any], user_message: str, db_manager: Optional[Any]) -> Dict[str, Any]:
    """Merge cross-check metadata into a copy for API responses."""
    trace = build_pipeline_trace(user_message, parsed_intent, db_manager)
    merged = merge_cross_check_into_dict(parsed_intent, trace["cross_check"])
    return {"parsed_intent": merged, "pipeline": trace}
