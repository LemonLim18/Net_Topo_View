from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db.manager import DatabaseManager
from .service import sync_syslog_server_inventory

router = APIRouter(prefix="/syslog", tags=["Syslog"])


def _db() -> DatabaseManager:
    return DatabaseManager("db/netauto.db")


class SyslogEventRow(BaseModel):
    id: int
    source_ip: str
    source_host: Optional[str] = None
    device_id: Optional[int] = None
    device_hostname: Optional[str] = None
    severity: Optional[str] = None
    facility: Optional[str] = None
    mnemonic: Optional[str] = None
    message: Optional[str] = None
    raw_message: str
    created_at: Optional[str] = None


@router.get("/events", response_model=List[SyslogEventRow])
async def list_syslog_events(
    limit: int = 100,
    device: Optional[str] = None,
    source_ip: Optional[str] = None,
    q: Optional[str] = None,
):
    """
    Return recent raw syslog events.
    - aggregated view: omit `device`
    - per-device view: set `device` to hostname or management IP
    """
    db = _db()
    try:
        limit = max(1, min(int(limit), 1000))
    except Exception:
        limit = 100

    where: List[str] = []
    params: List[Any] = []

    if device:
        where.append("(d.hostname = ? OR d.management_ip = ? OR e.source_ip = ?)")
        params.extend([device, device, device])
    if source_ip:
        where.append("e.source_ip = ?")
        params.append(source_ip)
    if q:
        where.append("(e.raw_message LIKE ? OR e.message LIKE ? OR d.hostname LIKE ?)")
        needle = f"%{q}%"
        params.extend([needle, needle, needle])

    query = (
        "SELECT e.id, e.source_ip, e.source_host, e.device_id, d.hostname AS device_hostname, "
        "e.severity, e.facility, e.mnemonic, e.message, e.raw_message, e.created_at "
        "FROM device_syslog_events e "
        "LEFT JOIN devices d ON e.device_id = d.id "
    )
    if where:
        query += "WHERE " + " AND ".join(where) + " "
    query += "ORDER BY e.created_at DESC LIMIT ?"
    params.append(limit)

    try:
        with db._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [
            SyslogEventRow(
                id=int(r["id"]),
                source_ip=str(r["source_ip"] or ""),
                source_host=r["source_host"],
                device_id=r["device_id"],
                device_hostname=r["device_hostname"],
                severity=r["severity"],
                facility=r["facility"],
                mnemonic=r["mnemonic"],
                message=r["message"],
                raw_message=str(r["raw_message"] or ""),
                created_at=r["created_at"],
            )
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to query syslog events: {exc}")


@router.get("/sources")
async def list_syslog_sources(limit: int = 200):
    """Return source/device aggregation for syslog inventory monitoring."""
    db = _db()
    try:
        limit = max(1, min(int(limit), 1000))
    except Exception:
        limit = 200

    query = (
        "WITH resolved AS ("
        "  SELECT "
        "    e.source_ip, "
        "    NULLIF(TRIM(COALESCE(e.source_host, '')), '') AS source_host, "
        "    COALESCE(e.device_id, d_by_ip.id) AS device_id, "
        "    NULLIF(TRIM(COALESCE(d_by_id.hostname, d_by_ip.hostname, '')), '') AS device_hostname, "
        "    e.created_at "
        "  FROM device_syslog_events e "
        "  LEFT JOIN devices d_by_id ON e.device_id = d_by_id.id "
        "  LEFT JOIN devices d_by_ip ON e.source_ip = d_by_ip.management_ip "
        ") "
        "SELECT "
        "  source_ip, "
        "  COALESCE(MAX(source_host), '') AS source_host, "
        "  MAX(device_id) AS device_id, "
        "  COALESCE(MAX(device_hostname), '') AS device_hostname, "
        "  COUNT(*) AS event_count, "
        "  MAX(created_at) AS last_seen "
        "FROM resolved "
        "GROUP BY source_ip, COALESCE(device_hostname, source_host, source_ip, 'unknown') "
        "ORDER BY last_seen DESC LIMIT ?"
    )
    try:
        with db._connect() as conn:
            rows = conn.execute(query, (limit,)).fetchall()
        return {
            "items": [
                {
                    "source_ip": r["source_ip"],
                    "source_host": r["source_host"] or None,
                    "device_id": r["device_id"],
                    "device_hostname": r["device_hostname"] or None,
                    "event_count": int(r["event_count"] or 0),
                    "last_seen": r["last_seen"],
                }
                for r in rows
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to query syslog sources: {exc}")


@router.post("/sync-server")
async def sync_remote_syslog_server(mode: Optional[str] = None):
    """
    Sync onboarding inventory into remote syslog server:
    - manages /etc/hosts mappings
    - updates rsyslog per-device dynamic fan-out rule
    - pre-creates per-device log files
    """
    db = _db()
    result = sync_syslog_server_inventory(db, mode=mode, force=True)
    return result
