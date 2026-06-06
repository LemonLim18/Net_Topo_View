"""
Resolve catalog operation specs from LLM action/feature (including recovery path).

Uses :func:`validation_engine.resolve_operation_spec` / schema recovery.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ansible.intent_pipeline.validation_engine import resolve_operation_spec


class OperationDetector:
    """Lookup operation_key + YAML spec + schema_source (catalog | adapter | synthetic)."""

    def resolve(
        self,
        action: str,
        feature: str,
        *,
        catalog: Any = None,
    ) -> Optional[Tuple[str, Dict[str, Any], str]]:
        return resolve_operation_spec(action, feature, catalog=catalog)
