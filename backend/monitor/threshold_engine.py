"""
Phase 5 — Threshold-Based Telemetry Monitoring Engine (UC34).

Configurable rules that evaluate InfluxDB metrics against thresholds
and raise alerts when breached.  Supports CRUD for rules, periodic
evaluation, and integrates with the incident system for auto-remediation.

Table schema (added to schema.sql):
    threshold_rules  — stores configurable alert rules
    threshold_alerts — stores triggered alert instances
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("monitor.threshold_engine")

router = APIRouter(prefix="/monitor/thresholds", tags=["Threshold Monitoring"])

# DI
_db = None

INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "netauto")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "telegraf")

try:
    from auth.auth import get_current_user as _auth_dep
except ImportError:
    def _auth_dep():  # type: ignore
        return {"username": "anonymous", "role": "admin"}


def setup_thresholds(db_manager) -> None:
    """Inject DB and ensure tables exist."""
    global _db
    _db = db_manager
    _ensure_tables()
    logger.info("Threshold monitoring engine ready")


def _ensure_tables():
    """Create threshold tables if they don't exist."""
    if _db is None:
        return
    with _db._connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS threshold_rules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                metric      TEXT NOT NULL,           -- e.g. 'cpu_usage', 'memory_usage', 'interface_errors'
                measurement TEXT DEFAULT 'snmp',     -- InfluxDB measurement
                field_key   TEXT DEFAULT '_value',   -- InfluxDB field
                operator    TEXT NOT NULL DEFAULT '>', -- > | < | >= | <= | == | !=
                threshold   REAL NOT NULL,
                severity    TEXT DEFAULT 'warning',  -- info | warning | critical
                device_filter TEXT,                  -- JSON: {"hostnames": [...], "vendor": "..."}
                cooldown_minutes INTEGER DEFAULT 15, -- don't re-alert within window
                enabled     BOOLEAN DEFAULT 1,
                auto_remediate BOOLEAN DEFAULT 0,
                remediation_playbook TEXT,           -- optional: YAML or template name
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS threshold_alerts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id     INTEGER NOT NULL REFERENCES threshold_rules(id) ON DELETE CASCADE,
                device      TEXT,
                metric      TEXT,
                value       REAL,
                threshold   REAL,
                severity    TEXT,
                status      TEXT DEFAULT 'active',   -- active | acknowledged | resolved | auto_resolved
                incident_id TEXT,                    -- FK to incidents (if auto-remediation triggered)
                created_at  TEXT DEFAULT (datetime('now')),
                resolved_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_threshold_alerts_rule ON threshold_alerts(rule_id);
            CREATE INDEX IF NOT EXISTS idx_threshold_alerts_status ON threshold_alerts(status);
        """)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RuleCreate(BaseModel):
    name: str = Field(..., min_length=1)
    metric: str
    measurement: str = "snmp"
    field_key: str = "_value"
    operator: str = ">"
    threshold: float
    severity: str = "warning"
    device_filter: Optional[Dict[str, Any]] = None
    cooldown_minutes: int = 15
    enabled: bool = True
    auto_remediate: bool = False
    remediation_playbook: Optional[str] = None


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    threshold: Optional[float] = None
    operator: Optional[str] = None
    severity: Optional[str] = None
    device_filter: Optional[Dict[str, Any]] = None
    cooldown_minutes: Optional[int] = None
    enabled: Optional[bool] = None
    auto_remediate: Optional[bool] = None
    remediation_playbook: Optional[str] = None


class RuleResponse(BaseModel):
    id: int
    name: str
    metric: str
    measurement: str
    field_key: str
    operator: str
    threshold: float
    severity: str
    device_filter: Optional[Dict[str, Any]] = None
    cooldown_minutes: int
    enabled: bool
    auto_remediate: bool
    remediation_playbook: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AlertResponse(BaseModel):
    id: int
    rule_id: int
    device: Optional[str] = None
    metric: Optional[str] = None
    value: Optional[float] = None
    threshold: Optional[float] = None
    severity: Optional[str] = None
    status: str
    incident_id: Optional[str] = None
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Rule CRUD
# ---------------------------------------------------------------------------

def _check_db():
    if _db is None:
        raise HTTPException(503, "Threshold engine not initialised")


def _row_dict(row) -> dict:
    d = dict(row)
    # Parse device_filter from JSON string
    if "device_filter" in d and isinstance(d["device_filter"], str):
        try:
            d["device_filter"] = json.loads(d["device_filter"])
        except Exception:
            d["device_filter"] = None
    return d


@router.get("/rules", response_model=List[RuleResponse])
async def list_rules(current_user: dict = Depends(_auth_dep)):
    _check_db()
    with _db._connect() as conn:
        rows = conn.execute("SELECT * FROM threshold_rules ORDER BY name").fetchall()
    return [RuleResponse(**_row_dict(r)) for r in rows]


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(body: RuleCreate, current_user: dict = Depends(_auth_dep)):
    _check_db()
    valid_ops = {">", "<", ">=", "<=", "==", "!="}
    if body.operator not in valid_ops:
        raise HTTPException(400, f"Invalid operator. Must be one of: {sorted(valid_ops)}")

    df_json = json.dumps(body.device_filter) if body.device_filter else None

    with _db._connect() as conn:
        cur = conn.execute(
            "INSERT INTO threshold_rules "
            "(name, metric, measurement, field_key, operator, threshold, severity, "
            "device_filter, cooldown_minutes, enabled, auto_remediate, remediation_playbook) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (body.name, body.metric, body.measurement, body.field_key, body.operator,
             body.threshold, body.severity, df_json, body.cooldown_minutes,
             body.enabled, body.auto_remediate, body.remediation_playbook),
        )
        row = conn.execute("SELECT * FROM threshold_rules WHERE id = ?", (cur.lastrowid,)).fetchone()
    return RuleResponse(**_row_dict(row))


@router.put("/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(rule_id: int, body: RuleUpdate, current_user: dict = Depends(_auth_dep)):
    _check_db()
    with _db._connect() as conn:
        existing = conn.execute("SELECT id FROM threshold_rules WHERE id = ?", (rule_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Rule not found")

        sets, params = [], []
        for fld in ("name", "threshold", "operator", "severity", "cooldown_minutes", "enabled",
                     "auto_remediate", "remediation_playbook"):
            val = getattr(body, fld, None)
            if val is not None:
                sets.append(f"{fld} = ?")
                params.append(val)
        if body.device_filter is not None:
            sets.append("device_filter = ?")
            params.append(json.dumps(body.device_filter))
        if sets:
            sets.append("updated_at = datetime('now')")
            params.append(rule_id)
            conn.execute(f"UPDATE threshold_rules SET {', '.join(sets)} WHERE id = ?", params)

        row = conn.execute("SELECT * FROM threshold_rules WHERE id = ?", (rule_id,)).fetchone()
    return RuleResponse(**_row_dict(row))


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, current_user: dict = Depends(_auth_dep)):
    _check_db()
    with _db._connect() as conn:
        existing = conn.execute("SELECT id FROM threshold_rules WHERE id = ?", (rule_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Rule not found")
        conn.execute("DELETE FROM threshold_rules WHERE id = ?", (rule_id,))
    return {"status": "deleted", "rule_id": rule_id}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@router.get("/alerts", response_model=List[AlertResponse])
async def list_alerts(
    status_filter: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(_auth_dep),
):
    _check_db()
    query = "SELECT * FROM threshold_alerts"
    params: list = []
    if status_filter:
        query += " WHERE status = ?"
        params.append(status_filter)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _db._connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [AlertResponse(**dict(r)) for r in rows]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int, current_user: dict = Depends(_auth_dep)):
    _check_db()
    with _db._connect() as conn:
        conn.execute(
            "UPDATE threshold_alerts SET status = 'acknowledged' WHERE id = ? AND status = 'active'",
            (alert_id,),
        )
    return {"status": "acknowledged", "alert_id": alert_id}


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int, current_user: dict = Depends(_auth_dep)):
    _check_db()
    with _db._connect() as conn:
        conn.execute(
            "UPDATE threshold_alerts SET status = 'resolved', resolved_at = datetime('now') "
            "WHERE id = ? AND status IN ('active', 'acknowledged')",
            (alert_id,),
        )
    return {"status": "resolved", "alert_id": alert_id}


# ---------------------------------------------------------------------------
# Evaluation Engine — called by scheduler or manually
# ---------------------------------------------------------------------------

@router.post("/evaluate", summary="Manually trigger threshold evaluation")
async def evaluate_now(current_user: dict = Depends(_auth_dep)):
    """Run all enabled rules against latest InfluxDB data."""
    _check_db()
    result = await run_in_threadpool(evaluate_all_rules)
    return result


@router.post("/evaluate-and-remediate", summary="Evaluate thresholds and auto-remediate breaches")
async def evaluate_and_remediate(current_user: dict = Depends(_auth_dep)):
    """
    Combined endpoint: evaluate all threshold rules AND trigger the
    auto-remediation pipeline for any new incidents created.

    This is the primary endpoint for the scheduler to call periodically
    to close the full threshold → alert → incident → remediation loop.
    """
    _check_db()

    # Phase 1: Evaluate all rules (creates alerts + incidents for auto_remediate rules)
    eval_result = await run_in_threadpool(evaluate_all_rules)

    # Phase 2: Process any pending auto-remediations via the incident orchestrator
    remediation_result = {"processed": 0, "succeeded": 0, "failed": 0}
    try:
        import httpx
        # Call the incidents auto-remediation endpoint internally
        # This uses the orchestrator's process_pending_auto_remediations
        from incidents.api import _orchestrator
        if _orchestrator is not None:
            remediation_result = await _orchestrator.process_pending_auto_remediations()
    except Exception as exc:
        remediation_result["error"] = str(exc)
        logger.warning("Auto-remediation processing failed: %s", exc)

    return {
        "evaluation": eval_result,
        "remediation": remediation_result,
    }


def evaluate_all_rules() -> Dict[str, Any]:
    """
    Core evaluation loop:
    1. Load all enabled rules
    2. For each rule, query InfluxDB for the metric
    3. Compare against threshold
    4. Create alert if breached (respecting cooldown)
    """
    if _db is None:
        return {"status": "error", "detail": "DB not initialised"}

    with _db._connect() as conn:
        rules = conn.execute("SELECT * FROM threshold_rules WHERE enabled = 1").fetchall()

    results = {"evaluated": 0, "alerts_created": 0, "errors": []}

    for rule_row in rules:
        rule = dict(rule_row)
        try:
            breaches = _query_metric_breaches(rule)
            for breach in breaches:
                _maybe_create_alert(rule, breach)
                results["alerts_created"] += 1
            results["evaluated"] += 1
        except Exception as e:
            results["errors"].append(f"rule {rule['id']} ({rule['name']}): {e}")

    return results


def _query_metric_breaches(rule: dict) -> List[Dict[str, Any]]:
    """Query InfluxDB for values that breach the rule threshold."""
    if not INFLUXDB_TOKEN:
        return []

    measurement = rule.get("measurement", "snmp")
    field_key = rule.get("field_key", "_value")
    op = rule.get("operator", ">")
    threshold = rule.get("threshold", 0)

    # Build Flux query
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => r._field == "{field_key}" or r._field == "{rule.get('metric', '')}")
  |> last()
  |> limit(n: 100)
'''

    # Device filter
    device_filter = rule.get("device_filter")
    if device_filter and isinstance(device_filter, str):
        try:
            device_filter = json.loads(device_filter)
        except Exception:
            device_filter = None

    try:
        body = flux.encode("utf-8")
        req = urllib.request.Request(
            f"{INFLUXDB_URL}/api/v2/query?org={INFLUXDB_ORG}",
            data=body,
            headers={
                "Authorization": f"Token {INFLUXDB_TOKEN}",
                "Content-Type": "application/vnd.flux",
                "Accept": "application/csv",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            csv_data = resp.read().decode("utf-8")

        breaches = []
        for line in csv_data.strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) < 7:
                continue
            try:
                value = float(parts[6]) if parts[6] else None
            except (ValueError, IndexError):
                continue
            if value is None:
                continue

            device = parts[4] if len(parts) > 4 else "unknown"

            # Apply device filter
            if device_filter:
                hostnames = device_filter.get("hostnames", [])
                if hostnames and device not in hostnames:
                    continue

            # Check threshold operator
            breached = False
            if op == ">" and value > threshold:
                breached = True
            elif op == "<" and value < threshold:
                breached = True
            elif op == ">=" and value >= threshold:
                breached = True
            elif op == "<=" and value <= threshold:
                breached = True
            elif op == "==" and value == threshold:
                breached = True
            elif op == "!=" and value != threshold:
                breached = True

            if breached:
                breaches.append({
                    "device": device,
                    "metric": rule.get("metric", ""),
                    "value": value,
                })

        return breaches

    except Exception as e:
        logger.debug("Threshold metric query failed: %s", e)
        return []


def _maybe_create_alert(rule: dict, breach: dict) -> None:
    """Create an alert if there isn't already an active one within cooldown."""
    if _db is None:
        return

    rule_id = rule["id"]
    device = breach.get("device", "unknown")
    cooldown = rule.get("cooldown_minutes", 15)

    with _db._connect() as conn:
        # Check cooldown — don't re-alert if there's a recent active/acknowledged alert
        recent = conn.execute(
            "SELECT id FROM threshold_alerts "
            "WHERE rule_id = ? AND device = ? AND status IN ('active', 'acknowledged') "
            "AND created_at > datetime('now', ?)",
            (rule_id, device, f"-{cooldown} minutes"),
        ).fetchone()

        if recent:
            return  # Within cooldown window

        conn.execute(
            "INSERT INTO threshold_alerts (rule_id, device, metric, value, threshold, severity) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rule_id, device, breach.get("metric"), breach.get("value"),
             rule.get("threshold"), rule.get("severity", "warning")),
        )

        # If auto-remediation is enabled, try to trigger an incident
        if rule.get("auto_remediate"):
            try:
                _trigger_auto_remediation(rule, breach)
            except Exception as e:
                logger.warning("Auto-remediation trigger failed for rule %d: %s", rule_id, e)


def _trigger_auto_remediation(rule: dict, breach: dict) -> Optional[str]:
    """
    Create an incident record for auto-remediation using IncidentStore.

    Returns the incident ID if created, or None.
    The incident is tagged with threshold metadata so the orchestrator's
    auto-remediation pipeline can pick it up.
    """
    try:
        from incidents.store import IncidentStore
        store = IncidentStore()

        device = breach.get("device", "unknown")
        anomaly_type = f"threshold_breach_{rule.get('metric', 'unknown')}"
        severity = rule.get("severity", "warning")

        inc_id = store.create(
            anomaly_type=anomaly_type,
            severity=severity,
            affected_devices=[device],
            metrics={
                "current": breach.get("value", 0),
                "baseline": 0.0,
                "threshold": rule.get("threshold", 0),
            },
        )

        # Attach remediation metadata by writing a partial analysis update
        remediation_playbook = rule.get("remediation_playbook") or ""
        store.update_analysis(
            inc_id,
            root_cause_summary=(
                f"Automatic threshold breach: rule '{rule.get('name', '?')}' — "
                f"{rule.get('metric', '?')} {rule.get('operator', '>')} "
                f"{rule.get('threshold', '?')} on device {device}. "
                f"Measured value: {breach.get('value', '?')}"
            ),
            confidence=0.9,
            possible_causes=[
                f"{rule.get('metric', 'metric')} exceeded configured threshold",
                f"Device {device} may be under abnormal load",
            ],
            proposed_description=(
                f"Auto-remediate threshold breach for {rule.get('metric', '?')} on {device}"
            ),
            proposed_playbook=remediation_playbook,
            estimated_impact="Automated fix — impact depends on remediation playbook",
            rollback_plan="Revert to previous configuration snapshot if remediation fails",
        )

        # Update the threshold_alerts row with the incident_id
        if _db is not None:
            with _db._connect() as conn:
                conn.execute(
                    "UPDATE threshold_alerts SET incident_id = ? "
                    "WHERE rule_id = ? AND device = ? AND incident_id IS NULL "
                    "ORDER BY created_at DESC LIMIT 1",
                    (inc_id, rule["id"], device),
                )

        logger.info(
            "Auto-remediation incident %s created for rule %d (%s)",
            inc_id, rule["id"], rule.get("name", "?"),
        )
        return inc_id

    except Exception as exc:
        logger.error("Failed to create auto-remediation incident: %s", exc)
        return None
