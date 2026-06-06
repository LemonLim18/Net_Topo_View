"""
M13: Security Module — Data Models
===================================
Dataclasses for security audits, findings, events, and rules.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SecurityRule:
    """Definition of a single CIS-style security check."""
    id: str                 # e.g. 'CIS-1.1.1'
    category: str           # e.g. 'access_control'
    severity: str           # critical | high | medium | low
    description: str        # Human-readable description
    vendor: str             # cisco_ios | huawei_vrp | juniper_junos | all
    check_fn: str           # function name in the checks module


@dataclass
class AuditResult:
    """Result of running a single security check."""
    rule: SecurityRule
    status: str             # pass | fail | error | skip
    evidence: str = ""      # config snippet showing the issue
    remediation: str = ""   # suggested fix command


@dataclass
class SecurityAudit:
    """Complete audit result for a device."""
    device_id: int
    score: float            # 0.0–100.0
    total_checks: int
    passed: int
    failed: int
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    results: List[AuditResult] = field(default_factory=list)
    id: Optional[int] = None
    created_at: Optional[str] = None


@dataclass
class SecurityEvent:
    """A detected security event / threat."""
    event_type: str         # brute_force | port_scan | malware | anomaly | rogue_device | suricata
    severity: str           # critical | high | medium | low | info
    source_ip: str = ""
    dest_ip: str = ""
    device_id: Optional[int] = None
    description: str = ""
    raw_data: Optional[Dict[str, Any]] = None
    status: str = "new"     # new | investigating | mitigated | false_positive
    id: Optional[int] = None
    created_at: Optional[str] = None
