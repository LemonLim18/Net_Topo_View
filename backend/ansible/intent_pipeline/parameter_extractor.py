"""
Parameter extraction via LLM (REFACTOR_INTENT_PARSER.md step [5]).

The main parser path uses shared prompts in ``prompt_builder``. This module provides:
- A thin async wrapper around the LLM provider for custom extractor prompts.
- Per-segment extraction hook for future multi-call pipelines.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def extract_json_from_llm_text(raw: str) -> Dict[str, Any]:
    """Parse JSON object from model output (tolerates extra prose)."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        from llm.intent import IntentParser

        return IntentParser(provider=None, db_manager=None)._extract_json(raw)


async def extract_intent_with_prompt(provider: Any, prompt: str) -> Dict[str, Any]:
    """
    Run a dedicated extraction prompt (single LLM call).

    ``provider`` must implement ``async generate(messages, tools, temperature)`` like ``llm.provider``.
    """
    from llm.provider import LLMMessage

    response = await provider.generate(
        messages=[LLMMessage(role="user", content=prompt)],
        tools=None,
        temperature=0.0,
    )
    text = (response.content or "").strip()
    return extract_json_from_llm_text(text)


def build_segment_extractor_prompt(
    segment_text: str,
    topology_context: str,
    operations_excerpt: str,
) -> str:
    """
    Compact extractor for one segment (optional second phase after split).
    """
    return f"""You extract ONE network automation intent from a segment of the user's request.
Output ONLY valid JSON with keys:
  "intent_type", "action", "feature", "vendor", "parameters", "scope",
  "risk_level", "justification", "requires_resolution",
  optional "canonical_operation" if the segment matches a fine-grained op from the doc
  (e.g. assign_vlan_to_access_port, advertise_network).

## Allowed operations (subset)
{operations_excerpt}

## Topology
{topology_context or "(none)"}

## Segment
{segment_text}
"""


async def extract_parameters_for_segment(
    provider: Any,
    segment_text: str,
    topology_context: str,
    operations_excerpt: str,
) -> Dict[str, Any]:
    prompt = build_segment_extractor_prompt(segment_text, topology_context, operations_excerpt)
    return await extract_intent_with_prompt(provider, prompt)
