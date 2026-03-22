"""
Public pipeline helpers (prompt build + validation + orchestration re-exports).
"""

from ansible.intent_pipeline.cross_check import cross_check_parsed_intent, merge_cross_check_into_dict
from ansible.intent_pipeline.interactive_ask import build_interactive_asks
from ansible.intent_pipeline.intent_splitter import segment_summary, split_multi_intent
from ansible.intent_pipeline.operation_adapter import apply_operation_adapter, list_canonical_operations
from ansible.intent_pipeline.orchestrator import (
    build_pipeline_trace,
    enrich_preview_response,
    merge_multi_intent_llm_response,
    prepare_llm_input_dict,
)
from ansible.intent_pipeline.parameter_extractor import (
    build_segment_extractor_prompt,
    extract_intent_with_prompt,
    extract_json_from_llm_text,
    extract_parameters_for_segment,
)
from ansible.intent_pipeline.prompt_builder import (
    build_multi_intent_user_prompt,
    build_v2_user_prompt,
    detect_categories_for_message,
)
from ansible.intent_pipeline.validation_engine import validate_intent_full
from ansible.intent_pipeline.validator import annotate_missing_fields, validate_llm_intent_dict

__all__ = [
    "annotate_missing_fields",
    "apply_operation_adapter",
    "build_interactive_asks",
    "build_multi_intent_user_prompt",
    "build_pipeline_trace",
    "build_segment_extractor_prompt",
    "build_v2_user_prompt",
    "cross_check_parsed_intent",
    "detect_categories_for_message",
    "enrich_preview_response",
    "extract_intent_with_prompt",
    "extract_json_from_llm_text",
    "extract_parameters_for_segment",
    "list_canonical_operations",
    "merge_cross_check_into_dict",
    "merge_multi_intent_llm_response",
    "prepare_llm_input_dict",
    "segment_summary",
    "split_multi_intent",
    "validate_intent_full",
    "validate_llm_intent_dict",
]
