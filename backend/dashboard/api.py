"""
Dashboard summary API — single endpoint that aggregates operational KPIs
from devices, interfaces, security audits, drift events, deployments,
security events, audit log, and context-health observability.

Designed to feed the NMC-style landing page in one round-trip.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from db.manager import DatabaseManager
from llm import network_tools
from llm.semantics import build_canonical_device_matrix
from llm.refresh_state import get_refresh_state_manager
from llm.retriever import get_retrieval_telemetry_snapshot
from rag.api import get_state_embedding_status_snapshot

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
logger = logging.getLogger("dashboard.api")

_db: Optional[DatabaseManager] = None


def setup_dashboard(db: DatabaseManager) -> None:
    """Inject the app-wide DatabaseManager singleton."""
    global _db
    _db = db


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _max_timestamp(*values: Optional[str]) -> Optional[str]:
    parsed = [(value, _parse_timestamp(value)) for value in values if value]
    parsed = [item for item in parsed if item[1] is not None]
    if not parsed:
        return None
    parsed.sort(key=lambda item: item[1])
    return parsed[-1][0]


def _age_seconds(value: Optional[str]) -> Optional[int]:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))


def _tool_payload(tool_func, *args, **kwargs) -> dict:
    raw = tool_func(*args, **kwargs)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _count_live_interfaces_from_topology(topo: dict) -> dict:
    nodes = topo.get("nodes", {}) if isinstance(topo, dict) else {}
    total = 0
    up = 0
    down = 0

    for node_data in nodes.values():
        iface_list = node_data.get("interfaceList", [])
        if not isinstance(iface_list, list):
            continue
        for iface in iface_list:
            if not isinstance(iface, dict):
                continue
            for _, raw_value in iface.items():
                total += 1
                text = str(raw_value or "").strip().lower()
                if (
                    "admin down" in text
                    or "administratively down" in text
                    or re.search(r"\bdown\b", text)
                ):
                    down += 1
                elif re.search(r"\bup\b", text):
                    up += 1

    return {"total": total, "up": up, "down": down}


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _status_from_live_interface_value(raw_value: object) -> str:
    text = str(raw_value or "").strip().lower()
    if not text:
        return "up"
    if (
        "admin down" in text
        or "administratively down" in text
        or "link down" in text
        or re.search(r"\bdown\b", text)
    ):
        return "down"
    # Live inventory payloads may expose IP/description instead of explicit state.
    # Per operational rule, anything not explicitly down is treated as up.
    return "up"


def _normalize_iface_name(name: str) -> str:
    return str(name or "").strip().lower()


def _build_context_health_summary(db: DatabaseManager) -> dict:
    refresh_operations = get_refresh_state_manager().get_operation_statuses()
    comparison = _tool_payload(network_tools.compare_planned_vs_live)
    devices = list(comparison.get("devices") or [])
    planned_only = list(comparison.get("planned_only") or [])
    live_only = list(comparison.get("live_only") or [])
    live_source_metadata = comparison.get("live_source_metadata") or {}
    inventory_source_metadata = comparison.get("inventory_source_metadata") or {}

    live_presence_count = sum(
        1 for device in devices
        if str(device.get("presence_state") or "") in {"planned_and_live", "live_only"}
    )
    stale_devices = live_presence_count if str(live_source_metadata.get("freshness_class") or "") in {"stale", "missing"} else 0

    recent_events = db.get_context_event_log(limit=20) if hasattr(db, "get_context_event_log") else []
    latest_event_at = recent_events[0]["created_at"] if recent_events else None
    inventory_status = refresh_operations.get("inventory", {})
    planned_topology_status = refresh_operations.get("planned_topology", {})
    last_netbox_sync_at = _max_timestamp(
        inventory_status.get("last_completed_at"),
        planned_topology_status.get("last_completed_at"),
        inventory_source_metadata.get("captured_at"),
    )

    return {
        "last_successful_crawl_at": _max_timestamp(
            refresh_operations.get("live_topology", {}).get("last_completed_at"),
            live_source_metadata.get("captured_at"),
        ),
        "last_netbox_sync_at": last_netbox_sync_at,
        "last_embedding_update_at": get_state_embedding_status_snapshot().get("last_embedded_at"),
        "stale_devices": {
            "count": stale_devices,
            "freshness_class": live_source_metadata.get("freshness_class"),
            "source": live_source_metadata.get("source"),
            "captured_at": live_source_metadata.get("captured_at"),
        },
        "missing_mappings": {
            "count": len(planned_only) + len(live_only),
            "planned_only_count": len(planned_only),
            "live_only_count": len(live_only),
            "planned_only": planned_only[:10],
            "live_only": live_only[:10],
        },
        "event_lag": {
            "latest_event_at": latest_event_at,
            "age_seconds": _age_seconds(latest_event_at),
            "recent_event_count": len(recent_events),
        },
        "retrieval_source_mix": get_retrieval_telemetry_snapshot(),
        "refresh_operations": refresh_operations,
        "inventory_freshness": inventory_source_metadata,
        "live_freshness": live_source_metadata,
    }


def _build_summary(db: DatabaseManager) -> dict:
    """Synchronous helper — runs in threadpool."""
    with db._connect() as conn:
        # ── Devices ──────────────────────────────────────
        row = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN status='active'  THEN 1 ELSE 0 END) AS up,"
            " SUM(CASE WHEN status='down'    THEN 1 ELSE 0 END) AS down,"
            " SUM(CASE WHEN status NOT IN ('active','down') THEN 1 ELSE 0 END) AS unknown"
            " FROM devices"
        ).fetchone()
        devices_from_inventory = {
            "total": row[0] or 0,
            "up": row[1] or 0,
            "down": row[2] or 0,
            "unknown": row[3] or 0,
        }
        try:
            inventory_records, inventory_source = network_tools._get_inventory_records()  # type: ignore[attr-defined]
            topo = db.get_topology() if hasattr(db, "get_topology") else {}
            live_nodes = topo.get("nodes", {}) if isinstance(topo, dict) else {}
            matrix = build_canonical_device_matrix(
                inventory_records,
                live_nodes,
                inventory_source=inventory_source,
            )
            if matrix:
                up_count = sum(1 for d in matrix if str(d.get("live_status") or "") == "up")
                planned_count = sum(1 for d in matrix if bool(d.get("inventory_present")))
                # Requested model:
                # down = planned inventory devices - online devices
                down_count = max(0, planned_count - up_count)
                devices = {
                    "total": planned_count,
                    "up": up_count,
                    "down": down_count,
                    "unknown": 0,
                }
            else:
                devices = devices_from_inventory
        except Exception:
            devices = devices_from_inventory

        # ── Interfaces ───────────────────────────────────
        row = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN status='up'   THEN 1 ELSE 0 END) AS up,"
            " SUM(CASE WHEN status='down' THEN 1 ELSE 0 END) AS down"
            " FROM interfaces"
        ).fetchone()
        interfaces_from_inventory = {
            "total": row[0] or 0,
            "up": row[1] or 0,
            "down": row[2] or 0,
        }
        if interfaces_from_inventory["up"] == 0 and interfaces_from_inventory["down"] == 0:
            row = conn.execute(
                "SELECT"
                " SUM(CASE WHEN status='active' THEN 1 ELSE 0 END),"
                " SUM(CASE WHEN status='inactive' THEN 1 ELSE 0 END)"
                " FROM interfaces"
            ).fetchone()
            interfaces_from_inventory["up"] = row[0] or 0
            interfaces_from_inventory["down"] = row[1] or 0

        try:
            topo = db.get_topology() if hasattr(db, "get_topology") else {}
            live_iface = _count_live_interfaces_from_topology(topo if isinstance(topo, dict) else {})
            # Hybrid planned-vs-live model:
            # - Total = planned inventory interfaces
            # - Up/Down derived from live status of planned interfaces when possible
            # - If a planned interface is missing in live view, count as down
            planned_rows = conn.execute(
                "SELECT d.hostname, i.name "
                "FROM interfaces i "
                "JOIN devices d ON d.id = i.device_id"
            ).fetchall()
            if planned_rows:
                planned_keys = {
                    (str(r[0] or "").strip().lower(), _normalize_iface_name(str(r[1] or "")))
                    for r in planned_rows
                    if str(r[0] or "").strip() and str(r[1] or "").strip()
                }

                live_nodes = topo.get("nodes", {}) if isinstance(topo, dict) else {}
                live_status_map = {}
                for node_name, node_data in live_nodes.items():
                    hostname = str(node_name or "").strip().lower()
                    iface_list = node_data.get("interfaceList", [])
                    if not isinstance(iface_list, list):
                        continue
                    for iface in iface_list:
                        if not isinstance(iface, dict):
                            continue
                        for iface_name, raw_value in iface.items():
                            key = (hostname, _normalize_iface_name(str(iface_name or "")))
                            live_status_map[key] = _status_from_live_interface_value(raw_value)

                up_count = 0
                down_count = 0
                for key in planned_keys:
                    status = live_status_map.get(key)
                    if status == "up":
                        up_count += 1
                    else:
                        # treat missing/unknown/down for planned interface as down operationally
                        down_count += 1

                interfaces = {
                    "total": len(planned_keys),
                    "up": up_count,
                    "down": down_count,
                }
            elif live_iface["total"] > 0:
                interfaces = {
                    "total": live_iface["total"],
                    "up": live_iface["up"],
                    "down": live_iface["down"],
                }
            else:
                interfaces = interfaces_from_inventory
        except Exception:
            interfaces = interfaces_from_inventory

        # ── Security ─────────────────────────────────────
        row = conn.execute(
            "SELECT AVG(score), COUNT(*) FROM security_audits"
        ).fetchone()
        avg_score = round(row[0], 1) if row[0] is not None else 0.0
        total_audits = row[1] or 0

        row = conn.execute(
            "SELECT COUNT(*) FROM security_events"
            " WHERE severity IN ('critical','high')"
            " AND created_at >= datetime('now','-24 hours')"
        ).fetchone()
        critical_events = row[0] or 0

        security = {
            "avg_score": avg_score,
            "total_audits": total_audits,
            "critical_events": critical_events,
        }

        # ── Drift ────────────────────────────────────────
        row = conn.execute(
            "SELECT COUNT(*),"
            " SUM(CASE WHEN has_drift=1 THEN 1 ELSE 0 END)"
            " FROM drift_events"
            " WHERE id IN (SELECT MAX(id) FROM drift_events GROUP BY device_id)"
        ).fetchone()
        total_checked = row[0] or 0
        drifted = row[1] or 0
        compliance_pct = round(
            ((total_checked - drifted) / total_checked * 100) if total_checked > 0 else 100.0, 1
        )
        drift = {
            "total_events": total_checked,
            "devices_with_drift": drifted,
            "compliance_pct": compliance_pct,
        }

        # ── Deployments ──────────────────────────────────
        has_ztp_jobs = _table_exists(conn, "ztp_publish_jobs")
        if has_ztp_jobs:
            row = conn.execute(
                "SELECT "
                " (SELECT COUNT(*) FROM jobs) + "
                " (SELECT COUNT(*) FROM ztp_publish_jobs)"
            ).fetchone()
            total_jobs = row[0] or 0

            row = conn.execute(
                "SELECT "
                " (SELECT COUNT(*) FROM jobs WHERE COALESCE(updated_at, created_at) >= datetime('now','-7 days')) + "
                " (SELECT COUNT(*) FROM ztp_publish_jobs WHERE COALESCE(updated_at, finished_at, started_at, created_at) >= datetime('now','-7 days'))"
            ).fetchone()
            recent_7d = row[0] or 0

            row = conn.execute(
                "SELECT status FROM ("
                " SELECT status, COALESCE(updated_at, created_at) AS ts FROM jobs"
                " UNION ALL "
                " SELECT status, COALESCE(updated_at, finished_at, started_at, created_at) AS ts FROM ztp_publish_jobs"
                ") ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            last_status = row[0] if row else None
        else:
            row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
            total_jobs = row[0] or 0

            row = conn.execute(
                "SELECT COUNT(*) FROM jobs"
                " WHERE COALESCE(updated_at, created_at) >= datetime('now','-7 days')"
            ).fetchone()
            recent_7d = row[0] or 0

            row = conn.execute(
                "SELECT status FROM jobs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_status = row[0] if row else None

        deployments = {
            "total_jobs": total_jobs,
            "recent_7d": recent_7d,
            "last_status": last_status,
        }

        # ── Recent Security Events ───────────────────────
        rows = conn.execute(
            "SELECT id, event_type, severity, description, created_at"
            " FROM security_events"
            " ORDER BY id DESC LIMIT 10"
        ).fetchall()
        recent_events = [
            {
                "id": r[0], "event_type": r[1], "severity": r[2],
                "description": r[3], "created_at": r[4],
            }
            for r in rows
        ]

        # ── Recent Audit Log ─────────────────────────────
        rows = conn.execute(
            "SELECT id, action, target, details, created_at"
            " FROM audit_log"
            " ORDER BY id DESC LIMIT 10"
        ).fetchall()
        recent_audit_log = [
            {
                "id": r[0], "action": r[1], "target": r[2],
                "details": r[3], "created_at": r[4],
            }
            for r in rows
        ]

        return {
            "devices": devices,
            "interfaces": interfaces,
            "security": security,
            "drift": drift,
            "deployments": deployments,
            "context_health": _build_context_health_summary(db),
            "recent_events": recent_events,
            "recent_audit_log": recent_audit_log,
        }


@router.get("/summary")
async def get_dashboard_summary():
    """
    One-shot aggregate endpoint for the NMC landing page.

    Returns device counts, interface counts, security score,
    drift compliance %, recent deployments, events, and audit log.
    """
    if _db is None:
        return {"error": "Dashboard not initialized"}
    return await run_in_threadpool(_build_summary, _db)
