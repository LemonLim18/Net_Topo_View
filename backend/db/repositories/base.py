"""
M14: Base Repository
Thin convenience wrapper around DatabaseManager for common patterns.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..manager import DatabaseManager


class BaseRepository:
    """Hold a reference to the shared DatabaseManager."""

    def __init__(self, db: "DatabaseManager") -> None:
        self._db = db
