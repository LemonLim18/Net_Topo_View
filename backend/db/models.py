"""
M14: Data Models
Dataclass representations of every database entity.
Used for type-safe data passing between the repository layer and the rest
of the backend.  These are intentionally plain dataclasses (no ORM coupling)
so the storage backend can be swapped freely.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# -----------------------------------------------------------
# Device
# -----------------------------------------------------------
@dataclass
class Device:
    """A network device in the inventory."""
    hostname: str
    management_ip: Optional[str] = None
    management_subnet_mask: Optional[str] = None
    management_interface: Optional[str] = None
    vendor: Optional[str] = None          # cisco_ios | huawei_vrp | juniper_junos
    model: Optional[str] = None
    os_version: Optional[str] = None
    serial_number: Optional[str] = None
    status: str = "unknown"               # planned | registered | provisioning | active | down | unknown
    role: str = "network-device"          # core | distribution | access | network-device
    source: str = "manual"                # manual | crawler | netbox
    last_polled: Optional[str] = None     # ISO-8601
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_port: int = 22
    netbox_id: Optional[int] = None
    id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# -----------------------------------------------------------
# Interface
# -----------------------------------------------------------
@dataclass
class Interface:
    """A single interface on a device."""
    device_id: int
    name: str                              # e.g. GigabitEthernet0/1
    ip_address: Optional[str] = None
    subnet_mask: Optional[str] = None
    mac_address: Optional[str] = None
    speed: Optional[str] = None
    status: str = "unknown"
    id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# -----------------------------------------------------------
# Link  (topology edge)
# -----------------------------------------------------------
@dataclass
class Link:
    """An adjacency between two devices / interfaces."""
    src_device_id: int
    dst_device_id: int
    src_interface_id: Optional[int] = None
    dst_interface_id: Optional[int] = None
    discovery_protocol: Optional[str] = None  # lldp | cdp | manual
    id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# -----------------------------------------------------------
# Configuration Snapshot
# -----------------------------------------------------------
@dataclass
class ConfigSnapshot:
    """A point-in-time backup of a device configuration."""
    device_id: int
    config_text: str
    config_hash: Optional[str] = None
    captured_at: Optional[str] = None
    description: Optional[str] = None
    # M26: Compliance/baseline metadata (optional; keeps old callers working)
    snapshot_type: str = "snapshot"              # snapshot | baseline | rollback | golden | actual
    source_type: Optional[str] = None            # deploy_pipeline | manual | import | scan
    normalized_config: Optional[str] = None      # canonicalized text used for diff/compliance
    checksum: Optional[str] = None               # checksum of normalized_config (or config_text)
    policy_version: Optional[int] = None
    intent_id: Optional[str] = None
    execution_id: Optional[int] = None
    approved_by: Optional[str] = None
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    owner_username: Optional[str] = None     # M15: resource ownership (user who created this snapshot)
    id: Optional[int] = None

    def __post_init__(self):
        if self.config_hash is None and self.config_text:
            self.config_hash = hashlib.sha256(
                self.config_text.encode("utf-8")
            ).hexdigest()
        # If a normalized checksum wasn't provided, populate it deterministically.
        # (This is used by compliance diffing; it is distinct from config_hash which
        # is computed from raw config_text and kept for backward compatibility.)
        if self.checksum is None:
            base = self.normalized_config if (self.normalized_config is not None) else self.config_text
            if base:
                self.checksum = hashlib.sha256(base.encode("utf-8")).hexdigest()


# -----------------------------------------------------------
# Audit Log Entry
# -----------------------------------------------------------
@dataclass
class AuditLogEntry:
    """An immutable record of a user / system action."""
    action: str                            # e.g. config_push, crawl_start
    user_id: Optional[str] = None
    target: Optional[str] = None           # e.g. device hostname
    details: Optional[str] = None          # JSON string
    created_at: Optional[str] = None
    id: Optional[int] = None


# -----------------------------------------------------------
# Drift Event
# -----------------------------------------------------------
@dataclass
class DriftEvent:
    """A configuration-drift detection result for a device."""
    device_id: int
    has_drift: bool
    hostname: Optional[str] = None
    severity: Optional[str] = None          # low | medium | high
    diff_text: Optional[str] = None
    timestamp: Optional[str] = None         # ISO-8601
    id: Optional[int] = None


# -----------------------------------------------------------
# Real-Time Topology Node  (in-memory only, not persisted)
# -----------------------------------------------------------
@dataclass
class TopologyNode:
    """
    Represents a device in the real-time topology cache.
    This is NOT stored in the SQL database.  It mirrors
    the JSON structure that mini_nce.py currently produces
    so the frontend can consume it without changes.
    """
    hostname: str
    mgmt_ip: Optional[str] = None
    interfaces: dict = field(default_factory=dict)
    connected_nodes: list = field(default_factory=list)
    edge_links: dict = field(default_factory=dict)
    status: str = "unknown"
