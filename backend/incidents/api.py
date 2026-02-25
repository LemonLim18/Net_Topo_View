"""
M25 Phase-2: Incidents REST API.

Endpoints:
    GET  /api/incidents                  — List incidents (filter by status)
    GET  /api/incidents/{id}             — Full incident report
    POST /api/incidents/{id}/approve     — Approve remediation playbook
    POST /api/incidents/{id}/dismiss     — Dismiss incident (false-positive)
    POST /api/incidents/{id}/resolve     — Mark as resolved after manual fix
    POST /api/incidents/ingest           — Internal: create incident from anomaly
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from incidents.store import IncidentStore
from incidents.models import (
    IncidentReport,
    IncidentSummary,
    DetectedAnomaly,
    AnomalyMetrics,
    CollectedContext,
    RootCauseAnalysis,
    ProposedSolution,
    PlaybookSummary,
)

logger = logging.getLogger("incidents.api")

router = APIRouter(prefix="/incidents", tags=["Incidents (M25)"])

# Singleton store — created lazily so tests can override the path
_store: Optional[IncidentStore] = None


def _get_store() -> IncidentStore:
    global _store
    if _store is None:
        _store = IncidentStore()
    return _store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_summary(row: dict) -> IncidentSummary:
    return IncidentSummary(
        id=row["id"],
        anomaly_type=row["anomaly_type"],
        severity=row["severity"],
        affected_devices=json.loads(row.get("affected_devices") or "[]"),
        detected_at=row.get("detected_at", ""),
        status=row["status"],
        root_cause_summary=row.get("root_cause_summary") or "",
        created_at=row.get("created_at", ""),
    )


def _row_to_report(row: dict) -> IncidentReport:
    anomaly = DetectedAnomaly(
        id=row["id"],
        type=row["anomaly_type"],
        severity=row["severity"],
        affected_devices=json.loads(row.get("affected_devices") or "[]"),
        detected_at=row.get("detected_at", ""),
        metrics=AnomalyMetrics(
            current=row.get("metrics_current") or 0.0,
            baseline=row.get("metrics_baseline") or 0.0,
            threshold=row.get("metrics_threshold") or 0.0,
        ),
    )

    ctx = CollectedContext(
        logs=json.loads(row.get("logs") or "[]"),
        configs=json.loads(row.get("configs") or "{}"),
        telemetry_snapshot=json.loads(row.get("telemetry_snapshot") or "{}"),
    )

    rca = RootCauseAnalysis(
        summary=row.get("root_cause_summary") or "",
        confidence=row.get("root_cause_confidence") or 0.0,
        possible_causes=json.loads(row.get("possible_causes") or "[]"),
    )

    playbook_yaml = row.get("proposed_playbook") or ""
    solution = ProposedSolution(
        description=row.get("proposed_description") or "",
        playbook=PlaybookSummary(
            name=f"remediate_{row['id'].lower().replace('-', '_')}.yml",
            filename=f"remediate_{row['id'].lower().replace('-', '_')}.yml",
            content=playbook_yaml,
            hosts=json.loads(row.get("affected_devices") or "[]"),
            tasks=[],
        ),
        estimated_impact=row.get("estimated_impact") or "",
        rollback_plan=row.get("rollback_plan") or "",
    )

    return IncidentReport(
        id=row["id"],
        anomaly=anomaly,
        collected_context=ctx,
        root_cause_analysis=rca,
        proposed_solution=solution,
        status=row["status"],
        approver=row.get("approver"),
        resolved_at=row.get("resolved_at"),
        created_at=row.get("created_at", ""),
        updated_at=row.get("updated_at", ""),
    )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    """Internal: ingest an anomaly detection as a new incident."""
    anomaly_type:     str
    severity:         str = "medium"
    affected_devices: List[str] = []
    metrics:          Dict[str, float] = {}
    detected_at:      Optional[str] = None
    logs:             List[str] = []


class AnalysisUpdate(BaseModel):
    """Update incident with AI-generated analysis (called by agent)."""
    root_cause_summary:   str   = ""
    confidence:           float = 0.0
    possible_causes:      List[str] = []
    proposed_description: str   = ""
    proposed_playbook:    str   = ""   # YAML
    estimated_impact:     str   = ""
    rollback_plan:        str   = ""
    logs:                 List[str] = []
    configs:              Dict[str, str] = {}
    telemetry:            Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=List[IncidentSummary])
@router.get("/", response_model=List[IncidentSummary])
async def list_incidents(
    status: Optional[str] = Query(None, description="Filter by status: open | investigating | approved | dismissed | resolved"),
    limit:  int           = Query(100, le=500),
):
    """
    List incidents, newest first.  Optionally filter by lifecycle status.
    """
    store = _get_store()
    rows  = store.list_all(status=status, limit=limit)
    return [_row_to_summary(r) for r in rows]


@router.get("/{incident_id}", response_model=IncidentReport)
async def get_incident(incident_id: str):
    """
    Return a full incident report including collected context,
    root-cause analysis, and the proposed remediation playbook.
    """
    store = _get_store()
    row   = store.get(incident_id)
    if not row:
        raise HTTPException(404, f"Incident {incident_id!r} not found")
    return _row_to_report(row)


@router.post("/{incident_id}/approve")
async def approve_incident(incident_id: str):
    """
    Approve the AI-generated remediation for this incident.

    The frontend should call POST /ansible/jobs/{job_id}/execute afterward
    to actually push the playbook.  This endpoint only transitions the
    lifecycle status to 'approved'.
    """
    store = _get_store()
    row   = store.get(incident_id)
    if not row:
        raise HTTPException(404, f"Incident {incident_id!r} not found")
    if row["status"] not in ("open", "investigating"):
        raise HTTPException(409, f"Incident is '{row['status']}', cannot approve")

    store.set_status(incident_id, "approved", approver="user")
    return {
        "status":      "approved",
        "incident_id": incident_id,
        "message":     "Remediation approved. Push the playbook to complete remediation.",
        "playbook":    row.get("proposed_playbook") or "",
    }


@router.post("/{incident_id}/dismiss")
async def dismiss_incident(incident_id: str):
    """Mark as false-positive / not actionable."""
    store = _get_store()
    row   = store.get(incident_id)
    if not row:
        raise HTTPException(404, f"Incident {incident_id!r} not found")
    if row["status"] in ("resolved", "dismissed"):
        raise HTTPException(409, f"Incident is already '{row['status']}'")

    store.set_status(incident_id, "dismissed")
    return {"status": "dismissed", "incident_id": incident_id}


@router.post("/{incident_id}/resolve")
async def resolve_incident(incident_id: str):
    """Mark as resolved after remediation has been applied."""
    store = _get_store()
    row   = store.get(incident_id)
    if not row:
        raise HTTPException(404, f"Incident {incident_id!r} not found")

    store.set_status(incident_id, "resolved")
    return {
        "status":      "resolved",
        "incident_id": incident_id,
        "resolved_at": datetime.utcnow().isoformat(),
    }


@router.post("/ingest")
async def ingest_incident(req: IngestRequest):
    """
    Internal endpoint — called by the anomaly detector or the agent
    when a new anomaly is found that warrants an incident.
    """
    store  = _get_store()
    inc_id = store.create(
        anomaly_type=req.anomaly_type,
        severity=req.severity,
        affected_devices=req.affected_devices,
        metrics={k: float(v) for k, v in req.metrics.items()},
        detected_at=req.detected_at,
    )
    # Persist any initial log lines
    if req.logs:
        store.update_analysis(inc_id, logs=req.logs)

    return {
        "status":      "created",
        "incident_id": inc_id,
        "message":     "Incident created. AI analysis will be populated shortly.",
    }


@router.put("/{incident_id}/analysis")
async def update_analysis(incident_id: str, req: AnalysisUpdate):
    """
    Write AI-generated RCA + proposed solution into the incident.
    Called by the background agent after it collects context.
    """
    store = _get_store()
    ok = store.update_analysis(
        incident_id,
        logs=req.logs,
        configs=req.configs,
        telemetry=req.telemetry,
        root_cause_summary=req.root_cause_summary,
        confidence=req.confidence,
        possible_causes=req.possible_causes,
        proposed_description=req.proposed_description,
        proposed_playbook=req.proposed_playbook,
        estimated_impact=req.estimated_impact,
        rollback_plan=req.rollback_plan,
    )
    if not ok:
        raise HTTPException(404, f"Incident {incident_id!r} not found")
    return {"status": "updated", "incident_id": incident_id}
