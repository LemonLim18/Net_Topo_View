from __future__ import annotations

from typing import List, Optional
import socket
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db.manager import DatabaseManager
from .service import (
    DEFAULT_DB_PATH,
    delete_profile,
    get_effective_profile,
    get_profile,
    list_profiles,
    upsert_profile,
)

router = APIRouter(prefix="/common-services", tags=["Common Services"])


def _db() -> DatabaseManager:
    return DatabaseManager(DEFAULT_DB_PATH)


class CommonServicesPayload(BaseModel):
    snmp_community: Optional[str] = None
    snmp_huawei_community: Optional[str] = None
    snmp_location: Optional[str] = None
    snmp_contact: Optional[str] = None
    snmp_host_ip: Optional[str] = None
    syslog_port: Optional[int] = None
    syslog_device_port: Optional[int] = None
    syslog_host_ip: Optional[str] = None
    ntp_server_ip: Optional[str] = None


@router.get("/profiles")
async def get_profiles():
    return {"items": list_profiles(DEFAULT_DB_PATH)}


@router.get("/profiles/{scope_type}/{scope_value}")
async def get_profile_by_scope(scope_type: str, scope_value: str):
    try:
        profile = get_profile(scope_type, scope_value, db_path=DEFAULT_DB_PATH)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not profile:
        raise HTTPException(404, "Profile not found")
    return profile


@router.put("/profiles/{scope_type}/{scope_value}")
async def save_profile(scope_type: str, scope_value: str, body: CommonServicesPayload):
    try:
        return upsert_profile(
            scope_type,
            scope_value,
            body.model_dump(exclude_unset=True),
            db_path=DEFAULT_DB_PATH,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/profiles/{scope_type}/{scope_value}")
async def remove_profile(scope_type: str, scope_value: str):
    try:
        deleted = delete_profile(scope_type, scope_value, db_path=DEFAULT_DB_PATH)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"deleted": deleted}


@router.get("/effective")
async def resolve_effective_profile(
    device_id: Optional[int] = None,
    role: Optional[str] = None,
    site: Optional[str] = None,
):
    resolved_role = role
    if device_id and not resolved_role:
        dev = _db().get_device_by_id(device_id)
        if dev:
            resolved_role = dev.role

    effective = get_effective_profile(
        DEFAULT_DB_PATH,
        site=site,
        role=resolved_role,
        device_id=device_id,
    )
    return {
        "site": site,
        "role": resolved_role,
        "device_id": device_id,
        "effective": effective,
    }


@router.post("/test-syslog")
async def test_syslog(site: Optional[str] = None, role: Optional[str] = None, device_id: Optional[int] = None):
    """Send a single UDP syslog test message to the effective syslog host/port."""
    effective = get_effective_profile(DEFAULT_DB_PATH, site=site, role=role, device_id=device_id)
    host = effective.get("syslog_host_ip")
    port = int(effective.get("syslog_port") or 6514)
    if not host:
        raise HTTPException(status_code=400, detail="Syslog host not configured in effective profile")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        # Use a Cisco ACL-deny formatted message so the project's syslog parser treats it as a security event
        msg = "%SEC-6-IPACCESSLOGP: list 101 denied tcp 192.168.1.100(12345) -> 10.0.0.5(80), 1 packet"
        sock.sendto(msg.encode("utf-8"), (host, port))
        sock.close()
        return {"ok": True, "host": host, "port": port}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send syslog test: {e}")


@router.post("/test-ntp")
async def test_ntp(site: Optional[str] = None, role: Optional[str] = None, device_id: Optional[int] = None):
    """Perform a simple NTP UDP query against the effective NTP server and return offset info."""
    effective = get_effective_profile(DEFAULT_DB_PATH, site=site, role=role, device_id=device_id)
    host = effective.get("ntp_server_ip")
    if not host:
        raise HTTPException(status_code=400, detail="NTP server not configured in effective profile")

    try:
        addr = (host, 123)
        packet = b"\x1b" + 47 * b"\0"
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3.0)
        sock.sendto(packet, addr)
        data, _ = sock.recvfrom(1024)
        sock.close()
        if len(data) < 48:
            raise ValueError("Short NTP response")
        import struct as _struct
        unpacked = _struct.unpack("!12I", data[0:48])
        tx_secs = unpacked[10]
        tx_frac = unpacked[11]
        tx = tx_secs + float(tx_frac) / 2**32
        unix_time = tx - 2208988800
        offset = unix_time - time.time()
        return {
            "ok": True,
            "server": host,
            "transmit_time": time.ctime(unix_time),
            "offset_seconds": offset,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"NTP query failed: {e}")
