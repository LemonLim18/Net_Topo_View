"""
M14: Database Layer
Provides persistent storage (SQLite/PostgreSQL) for inventory, audit logs,
and configuration snapshots, plus an in-memory cache for real-time topology.
"""
from .manager import DatabaseManager
from .models import Device, Interface, Link, ConfigSnapshot, AuditLogEntry, DriftEvent

__all__ = [
    "DatabaseManager",
    "Device",
    "Interface",
    "Link",
    "ConfigSnapshot",
    "AuditLogEntry",
    "DriftEvent",
]
