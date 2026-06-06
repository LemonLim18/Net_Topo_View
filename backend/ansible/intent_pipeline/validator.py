"""
Stable import path for intent validation.

Implementation lives in :mod:`validation_engine` (catalog + recovery + forbidden fields).
"""

from __future__ import annotations

from ansible.intent_pipeline.validation_engine import (
    annotate_missing_fields,
    resolve_operation_spec,
    validate_intent_full,
    validate_llm_intent_dict,
)

__all__ = [
    "annotate_missing_fields",
    "resolve_operation_spec",
    "validate_intent_full",
    "validate_llm_intent_dict",
]
