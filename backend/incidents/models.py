"""
M25 Phase-2: Incident data models (Pydantic v2).

These mirror the TypeScript interfaces defined in FRONTEND_CONTEXT.md
so the REST layer can serialize/deserialize without manual translation.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── Anomaly  ─────────────────────────────────────────────────────────────────

class AnomalyMetrics(BaseModel):
    current:   float = 0.0
    baseline:  float = 0.0
    threshold: float = 0.0


class DetectedAnomaly(BaseModel):
    """Represents a single detected anomalous condition."""

    id:                str
    type:              str   # bandwidth_drop | link_failure | interface_error |
                              # latency_spike | packet_loss | cpu_high | memory_high
    severity:          str   # critical | high | medium | low | info
    affected_devices:  List[str] = Field(default_factory=list)
    detected_at:       str   = ""
    metrics:           AnomalyMetrics = Field(default_factory=AnomalyMetrics)


# ── Collected Context  ────────────────────────────────────────────────────────

class CollectedContext(BaseModel):
    logs:               List[str]            = Field(default_factory=list)
    configs:            Dict[str, str]       = Field(default_factory=dict)  # hostname → snippet
    telemetry_snapshot: Dict[str, Any]       = Field(default_factory=dict)


# ── RCA  ─────────────────────────────────────────────────────────────────────

class RootCauseAnalysis(BaseModel):
    summary:        str   = ""
    confidence:     float = 0.0   # 0.0 – 1.0
    possible_causes: List[str] = Field(default_factory=list)


# ── Proposed Solution  ────────────────────────────────────────────────────────

class PlaybookSummary(BaseModel):
    """Brief description of the proposed playbook (inline)."""
    name:     str = ""
    filename: str = ""
    content:  str = ""    # YAML
    hosts:    List[str] = Field(default_factory=list)
    tasks:    List[Dict[str, Any]] = Field(default_factory=list)


class ProposedSolution(BaseModel):
    description:      str            = ""
    playbook:         PlaybookSummary = Field(default_factory=PlaybookSummary)
    estimated_impact: str            = ""
    rollback_plan:    str            = ""


# ── Full Incident Report  ─────────────────────────────────────────────────────

class IncidentReport(BaseModel):
    """Full incident report returned to the frontend."""

    id:                  str
    anomaly:             DetectedAnomaly
    collected_context:   CollectedContext     = Field(default_factory=CollectedContext)
    root_cause_analysis: RootCauseAnalysis    = Field(default_factory=RootCauseAnalysis)
    proposed_solution:   ProposedSolution     = Field(default_factory=ProposedSolution)

    # Lifecycle
    status:      str           = "open"      # open | investigating | approved | dismissed | resolved
    approver:    Optional[str] = None
    resolved_at: Optional[str] = None

    created_at:  str = ""
    updated_at:  str = ""


# ── List summary (no playbook content) ───────────────────────────────────────

class IncidentSummary(BaseModel):
    id:               str
    anomaly_type:     str
    severity:         str
    affected_devices: List[str]
    detected_at:      str
    status:           str
    root_cause_summary: str = ""
    created_at:       str = ""
