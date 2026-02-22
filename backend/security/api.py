"""
M13: Security Module API — REST endpoints for audits, events, and scoring.
"""

import json
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from db.manager import DatabaseManager
from security.engine import SecurityAuditEngine
from security.models import SecurityEvent
from security.threat.suricata import SuricataParser
from security.threat.anomaly_detector import AnomalyDetector
from security.threat.rogue_detector import RogueDeviceDetector
from security.response.responder import IncidentResponder
from security.response.playbooks import PlaybookEngine
from security.cve.matcher import CVEMatcher

logger = logging.getLogger("security.api")

router = APIRouter(prefix="/security", tags=["Security (M13)"])

# Singletons
_engine = SecurityAuditEngine()
_suricata_parser = SuricataParser()
_anomaly_detector = AnomalyDetector()
_responder = IncidentResponder()
_playbook_engine = PlaybookEngine()
_cve_matcher = CVEMatcher()



# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class AuditSummaryResponse(BaseModel):
    device_id: int
    score: float
    total_checks: int
    passed: int
    failed: int
    critical: int
    high: int
    medium: int
    low: int

class FindingResponse(BaseModel):
    rule_id: str
    category: str
    severity: str
    status: str
    description: str
    evidence: str
    remediation: str

class AuditDetailResponse(BaseModel):
    device_id: int
    score: float
    total_checks: int
    passed: int
    failed: int
    critical: int
    high: int
    medium: int
    low: int
    findings: List[FindingResponse]

class EventResponse(BaseModel):
    id: Optional[int] = None
    event_type: str
    severity: str
    source_ip: str
    dest_ip: str
    device_id: Optional[int] = None
    description: str
    status: str
    created_at: Optional[str] = None

class IngestAlertRequest(BaseModel):
    event_type: str
    severity: str
    source_ip: str = ""
    dest_ip: str = ""
    device_id: Optional[int] = None
    description: str = ""
    raw_data: Optional[dict] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/audit/{device_id}", response_model=AuditDetailResponse)
async def run_audit(device_id: int):
    """Run a security audit on a single device."""
    db = DatabaseManager()

    device = db.get_device_by_id(device_id)
    if not device:
        raise HTTPException(404, f"Device {device_id} not found")

    # Get latest config
    history = db.get_config_history(device_id, limit=1)
    if not history:
        raise HTTPException(404, f"No config snapshot for device {device_id}")

    config_text = history[0].config_text
    vendor = device.vendor or "cisco_ios"

    audit = _engine.run_audit(device_id, config_text, vendor)

    # Save to database
    try:
        _save_audit(db, audit)
    except Exception as e:
        logger.error("Failed to save audit: %s", e)

    # Build response
    findings = [
        FindingResponse(
            rule_id=r.rule.id,
            category=r.rule.category,
            severity=r.rule.severity,
            status=r.status,
            description=r.rule.description,
            evidence=r.evidence,
            remediation=r.remediation,
        )
        for r in audit.results
    ]

    return AuditDetailResponse(
        device_id=audit.device_id,
        score=audit.score,
        total_checks=audit.total_checks,
        passed=audit.passed,
        failed=audit.failed,
        critical=audit.critical,
        high=audit.high,
        medium=audit.medium,
        low=audit.low,
        findings=findings,
    )


@router.post("/audit/all")
async def run_audit_all(background_tasks: BackgroundTasks):
    """Run security audits on ALL devices (background task)."""
    background_tasks.add_task(_run_all_audits)
    return {"status": "started", "message": "Network-wide security audit running in background"}


@router.get("/audits", response_model=List[AuditSummaryResponse])
async def list_audits(device_id: Optional[int] = None, limit: int = 50):
    """List recent audit summaries."""
    db = DatabaseManager()
    try:
        with db._connect() as conn:
            if device_id:
                rows = conn.execute(
                    "SELECT * FROM security_audits WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
                    (device_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM security_audits ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        return [
            AuditSummaryResponse(
                device_id=r["device_id"], score=r["score"],
                total_checks=r["total_checks"], passed=r["passed"],
                failed=r["failed"], critical=r["critical"],
                high=r["high"], medium=r["medium"], low=r["low"],
            )
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/audits/{device_id}/findings", response_model=List[FindingResponse])
async def get_findings(device_id: int):
    """Get the latest audit findings for a device."""
    db = DatabaseManager()
    try:
        with db._connect() as conn:
            # Get latest audit
            audit_row = conn.execute(
                "SELECT id FROM security_audits WHERE device_id = ? ORDER BY created_at DESC LIMIT 1",
                (device_id,)
            ).fetchone()
            if not audit_row:
                raise HTTPException(404, f"No audits for device {device_id}")

            rows = conn.execute(
                "SELECT * FROM audit_findings WHERE audit_id = ?",
                (audit_row["id"],)
            ).fetchall()
        return [
            FindingResponse(
                rule_id=r["rule_id"], category=r["category"],
                severity=r["severity"], status=r["status"],
                description=r["description"] or "",
                evidence=r["evidence"] or "",
                remediation=r["remediation"] or "",
            )
            for r in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/score/{device_id}")
async def get_score(device_id: int):
    """Get the latest security score for a device."""
    db = DatabaseManager()
    try:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT score, created_at FROM security_audits WHERE device_id = ? ORDER BY created_at DESC LIMIT 1",
                (device_id,)
            ).fetchone()
        if not row:
            raise HTTPException(404, f"No audits for device {device_id}")
        return {"device_id": device_id, "score": row["score"], "as_of": row["created_at"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/events", response_model=List[EventResponse])
async def list_events(limit: int = 50, severity: Optional[str] = None):
    """List security events / threats."""
    db = DatabaseManager()
    try:
        with db._connect() as conn:
            if severity:
                rows = conn.execute(
                    "SELECT * FROM security_events WHERE severity = ? ORDER BY created_at DESC LIMIT ?",
                    (severity, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM security_events ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        return [
            EventResponse(
                id=r["id"], event_type=r["event_type"],
                severity=r["severity"], source_ip=r["source_ip"] or "",
                dest_ip=r["dest_ip"] or "",
                device_id=r["device_id"],
                description=r["description"] or "",
                status=r["status"], created_at=r["created_at"],
            )
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/events", response_model=EventResponse)
async def ingest_alert(req: IngestAlertRequest):
    """Ingest an external security alert (Suricata webhook, manual, etc.)."""
    db = DatabaseManager()
    try:
        with db._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO security_events 
                   (event_type, severity, source_ip, dest_ip, device_id, description, raw_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (req.event_type, req.severity, req.source_ip, req.dest_ip,
                 req.device_id, req.description,
                 json.dumps(req.raw_data) if req.raw_data else None)
            )
            conn.commit()
            event_id = cursor.lastrowid
        return EventResponse(
            id=event_id, event_type=req.event_type,
            severity=req.severity, source_ip=req.source_ip,
            dest_ip=req.dest_ip, device_id=req.device_id,
            description=req.description, status="new",
        )
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _save_audit(db: DatabaseManager, audit):
    """Persist an audit and its findings to the database."""
    with db._connect() as conn:
        cursor = conn.execute(
            """INSERT INTO security_audits 
               (device_id, score, total_checks, passed, failed, critical, high, medium, low)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (audit.device_id, audit.score, audit.total_checks,
             audit.passed, audit.failed, audit.critical,
             audit.high, audit.medium, audit.low)
        )
        audit_id = cursor.lastrowid

        for r in audit.results:
            conn.execute(
                """INSERT INTO audit_findings 
                   (audit_id, rule_id, category, severity, status, description, evidence, remediation)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (audit_id, r.rule.id, r.rule.category, r.rule.severity,
                 r.status, r.rule.description, r.evidence, r.remediation)
            )
        conn.commit()


def _run_all_audits():
    """Background task: audit all devices."""
    db = DatabaseManager()
    audits = _engine.run_audit_all(db)
    for audit in audits:
        try:
            _save_audit(db, audit)
        except Exception as e:
            logger.error("Failed to save audit for device %d: %s", audit.device_id, e)
    logger.info("Network-wide audit complete: %d devices audited", len(audits))


def _save_event(db: DatabaseManager, event: SecurityEvent) -> int:
    """Persist a SecurityEvent to the database. Returns the event ID."""
    with db._connect() as conn:
        cursor = conn.execute(
            """INSERT INTO security_events
               (event_type, severity, source_ip, dest_ip, device_id, description, raw_data, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event.event_type, event.severity, event.source_ip, event.dest_ip,
             event.device_id, event.description,
             json.dumps(event.raw_data) if event.raw_data else None,
             event.status)
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# M13b: Threat Detection Endpoints
# ---------------------------------------------------------------------------

@router.post("/alerts/suricata")
async def ingest_suricata_alert(payload: dict):
    """
    Ingest a Suricata eve.json alert via webhook.
    The payload should be a single Suricata eve.json alert entry.
    """
    event = _suricata_parser.parse_alert(payload)
    if not event:
        return {"status": "skipped", "message": "Not an alert-type event"}

    db = DatabaseManager()
    event_id = _save_event(db, event)
    return {"status": "ingested", "event_id": event_id, "severity": event.severity}


@router.post("/threats/rogue-scan")
async def scan_rogue_devices():
    """Scan the network topology for unknown / rogue devices."""
    db = DatabaseManager()
    detector = RogueDeviceDetector(db)
    events = detector.scan()

    # Persist detected rogue events
    saved_ids = []
    for event in events:
        try:
            eid = _save_event(db, event)
            saved_ids.append(eid)
        except Exception as e:
            logger.error("Failed to save rogue event: %s", e)

    return {
        "status": "complete",
        "rogue_devices_found": len(events),
        "event_ids": saved_ids,
    }


@router.post("/threats/analyze")
async def analyze_threats(limit: int = 100):
    """
    Analyze recent security events for anomaly patterns
    (brute force, port scan, traffic spikes).
    """
    db = DatabaseManager()
    try:
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM security_events ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()

        # Convert rows to SecurityEvent objects
        events = [
            SecurityEvent(
                event_type=r["event_type"],
                severity=r["severity"],
                source_ip=r["source_ip"] or "",
                dest_ip=r["dest_ip"] or "",
                device_id=r["device_id"],
                description=r["description"] or "",
                raw_data=json.loads(r["raw_data"]) if r["raw_data"] else None,
                status=r["status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

        anomalies = _anomaly_detector.analyze_events(events)

        # Save detected anomalies
        saved_ids = []
        for anomaly in anomalies:
            try:
                eid = _save_event(db, anomaly)
                saved_ids.append(eid)
            except Exception as e:
                logger.error("Failed to save anomaly: %s", e)

        return {
            "status": "complete",
            "events_analyzed": len(events),
            "anomalies_detected": len(anomalies),
            "event_ids": saved_ids,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# M13c: Incident Response Endpoints
# ---------------------------------------------------------------------------

@router.post("/respond/{event_id}")
async def respond_to_event(event_id: int, vendor: str = "cisco_ios"):
    """Generate a remediation response for a security event."""
    db = DatabaseManager()
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT * FROM security_events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Event {event_id} not found")

        event = SecurityEvent(
            event_type=row["event_type"], severity=row["severity"],
            source_ip=row["source_ip"] or "", dest_ip=row["dest_ip"] or "",
            device_id=row["device_id"], description=row["description"] or "",
            raw_data=json.loads(row["raw_data"]) if row["raw_data"] else None,
            status=row["status"], id=row["id"],
        )

        action = _responder.respond(event, vendor)
        return {
            "action_type": action.action_type,
            "severity": action.severity,
            "description": action.description,
            "config_snippet": action.config_snippet,
            "vendor": action.vendor,
            "requires_approval": action.requires_approval,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/playbook/{event_id}")
async def execute_playbook(event_id: int, vendor: str = "cisco_ios"):
    """Execute the matching playbook for a security event."""
    db = DatabaseManager()
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT * FROM security_events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Event {event_id} not found")

        event = SecurityEvent(
            event_type=row["event_type"], severity=row["severity"],
            source_ip=row["source_ip"] or "", dest_ip=row["dest_ip"] or "",
            device_id=row["device_id"], description=row["description"] or "",
            raw_data=json.loads(row["raw_data"]) if row["raw_data"] else None,
            status=row["status"], id=row["id"],
        )

        playbook = _playbook_engine.get_playbook(event.event_type)
        if not playbook:
            return {"status": "no_playbook", "message": f"No playbook for event type: {event.event_type}"}

        result = _playbook_engine.execute(playbook.name, event, vendor)
        return {
            "playbook_name": result.playbook_name,
            "status": result.status,
            "steps_executed": result.steps_executed,
            "config_snippet": result.response_action.config_snippet if result.response_action else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/playbooks")
async def list_playbooks():
    """List all available incident response playbooks."""
    return _playbook_engine.list_playbooks()


# ---------------------------------------------------------------------------
# M13d: CVE Correlation Endpoints
# ---------------------------------------------------------------------------

@router.get("/cve/{device_id}")
async def get_device_cves(device_id: int):
    """Get matching CVEs for a device based on its vendor and OS version."""
    db = DatabaseManager()
    device = db.get_device_by_id(device_id)
    if not device:
        raise HTTPException(404, f"Device {device_id} not found")

    vendor = device.vendor or ""
    os_version = device.os_version or ""
    if not vendor or not os_version:
        return {"device_id": device_id, "cves": [], "message": "Missing vendor or OS version"}

    cves = _cve_matcher.match_device(vendor, os_version)
    return {
        "device_id": device_id,
        "hostname": device.hostname,
        "vendor": vendor,
        "os_version": os_version,
        "cve_count": len(cves),
        "cves": cves,
    }


@router.get("/cve")
async def get_cve_stats():
    """Get CVE database statistics."""
    return _cve_matcher.get_stats()
