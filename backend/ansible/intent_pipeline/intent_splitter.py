"""
Multi-intent splitting (REFACTOR_INTENT_PARSER.md — step [2]).
"""

from __future__ import annotations

import re
from typing import List

# Split before a second imperative clause: "... and configure ...", "... and apply ..."
_SPLIT_AND_VERB = re.compile(
    r"\s+and\s+(?=(?:then\s+)?(?:apply|configure|add|set|enable|remove|delete|rename|assign|advertise|permit|shutdown|create)\b)",
    re.IGNORECASE,
)


def split_multi_intent(text: str) -> List[str]:
    """
    Split user text into segments when multiple independent automation intents are present.

    Rules:
    - ``;`` and blank-line paragraphs start a new top-level segment.
    - Within a segment, `` and `` before another imperative verb starts a new segment.
    - If no split applies, returns a single-element list with the original stripped text.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    blocks = re.split(r"\s*;\s*|\n\s*\n+", raw)
    segments: List[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        parts = _SPLIT_AND_VERB.split(block)
        for p in parts:
            p = p.strip()
            if p:
                segments.append(p)

    if len(segments) <= 1:
        return [raw]
    return segments


def segment_summary(segments: List[str]) -> str:
    """Short string for prompts / tracing."""
    return f"{len(segments)} segment(s): " + " | ".join(s[:60] + ("…" if len(s) > 60 else "") for s in segments)
