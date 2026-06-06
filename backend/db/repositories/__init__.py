"""
M14: Base Repository
Thin convenience wrapper around DatabaseManager for common patterns.
Concrete repositories (DeviceRepository, TopologyRepository) inherit
from this and add domain-specific queries.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..manager import DatabaseManager


class BaseRepository:
    """
    Every repository holds a reference to the shared DatabaseManager
    so it can access the connection helper and the in-memory cache.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
