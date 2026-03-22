"""
Category routing from user text (schema + keyword data only).

Delegates to :mod:`keyword_manager` and the loaded :class:`schema_loader.IntentCatalog`.
"""

from __future__ import annotations

from typing import Any, List

from ansible.intent_pipeline.keyword_manager import (
    build_operation_hint_block,
    detect_categories_for_message,
    merge_keyword_map_into_catalog,
)
from ansible.intent_pipeline.schema_loader import get_catalog


class CategoryDetector:
    """Generic category detection; no per-category Python branches."""

    def __init__(self, catalog: Any = None) -> None:
        self.catalog = catalog or get_catalog()
        merge_keyword_map_into_catalog(self.catalog)

    def detect(self, user_message: str) -> List[str]:
        return detect_categories_for_message(user_message, self.catalog)

    def prompt_hint_block(self, user_message: str) -> str:
        return build_operation_hint_block(user_message, self.catalog)
