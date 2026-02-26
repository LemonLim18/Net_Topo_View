"""
M8 Drift Detection — LLM-based Drift Classifier.

Calls the M5 LLM provider to classify a drift finding with a
semantic severity label (LOW / MEDIUM / HIGH / CRITICAL) and a
brief human-readable rationale.  The result supplements the
rule-based severity already computed by DriftComparator.
"""

import json
import logging
from typing import Optional

from drift.models import DriftEvent
from llm.provider import get_llm_provider

logger = logging.getLogger("drift.classifier")

_CLASSIFY_PROMPT = """\
You are a network security expert.  Analyse the following configuration \
drift found on device "{hostname}" and classify its severity.

Diff (unified format):
---
{diff}
---

Current rule-based severity: {rule_severity}

Respond ONLY with a JSON object (no markdown) in this exact format:
{{
  "severity": "<LOW|MEDIUM|HIGH|CRITICAL>",
  "rationale": "<one or two sentence explanation>",
  "risk_areas": ["<area1>", "<area2>"]
}}

Risk areas should be chosen from: ["routing", "access_control", "authentication",
"encryption", "logging", "ntp", "management_plane", "data_plane", "unknown"]
"""


class DriftClassifier:
    """
    Uses the configured LLM (Gemini / Ollama) to enrich a DriftEvent
    with an AI-generated severity and rationale.

    If the LLM call fails for any reason, the original rule-based
    severity is kept unchanged and the method returns gracefully.
    """

    def __init__(self):
        self.llm = get_llm_provider()

    def classify(self, event: DriftEvent) -> DriftEvent:
        """
        Enrich *event* in-place with AI severity.

        Returns the (possibly mutated) event so callers can chain calls.
        """
        if not event.has_drift or not event.diff_text:
            return event

        prompt = _CLASSIFY_PROMPT.format(
            hostname=event.hostname,
            diff=event.diff_text[:4000],  # guard against huge diffs
            rule_severity=event.severity.upper(),
        )

        try:
            raw = self.llm.generate_sync(prompt, temperature=0.1)
            parsed = self._parse(raw)
            if parsed:
                ai_sev = parsed.get("severity", "").upper()
                if ai_sev in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                    # Escalate if AI classifies worse than rule-based; never downgrade
                    severity_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
                    if severity_rank.get(ai_sev, 0) > severity_rank.get(event.severity.upper(), 0):
                        logger.info(
                            "Device %s: AI escalates drift severity %s → %s",
                            event.hostname, event.severity, ai_sev.lower(),
                        )
                        event.severity = ai_sev.lower()
                # Attach extra metadata if the dataclass supports it
                event.__dict__.setdefault("ai_rationale", parsed.get("rationale", ""))
                event.__dict__.setdefault("risk_areas",   parsed.get("risk_areas", []))
        except Exception as exc:  # noqa: BLE001
            logger.warning("DriftClassifier LLM call failed for %s: %s", event.hostname, exc)

        return event

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(raw: str) -> Optional[dict]:
        """Extract the JSON object from the LLM response."""
        try:
            # Strip markdown fences if present
            text = raw.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                text = "\n".join(
                    l for l in lines if not l.startswith("```")
                )
            return json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("Could not parse classifier response: %s | raw=%r", exc, raw[:200])
            return None
