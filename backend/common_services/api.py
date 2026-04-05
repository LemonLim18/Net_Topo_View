from __future__ import annotations

from typing import List, Optional

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
    snmp_location: Optional[str] = None
    snmp_contact: Optional[str] = None
    snmp_host_ip: Optional[str] = None
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
        return upsert_profile(scope_type, scope_value, body.model_dump(), db_path=DEFAULT_DB_PATH)
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
