"""
M0 — Onboarding API
Endpoints for the Device Onboarding Wizard.

Flow
----
1.  Frontend calls GET /onboarding/devices  → list of registered devices
2.  User picks one; frontend calls POST /onboarding/generate/{device_id}
3.  Backend looks up device, builds vendor template, returns config string
4.  User edits in Monaco editor and clicks "Download Script"
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from auth.credentials import CredentialVault
from auth.rbac import require_admin, require_any
from common_services.service import get_effective_profile
from config_engine.renderer import TemplateRenderer
from .config_generator import OnboardingConfigGenerator
from .ztp_publish import (
    RemoteLinuxExecutor,
    create_publish_job,
    get_publish_job,
    get_publish_manifest,
    list_publish_jobs,
    run_publish_job,
)
from db.manager import DatabaseManager

logger = logging.getLogger("onboarding.api")

router = APIRouter(prefix="/onboarding", tags=["Onboarding (M0)"])

DB_PATH = "db/netauto.db"
_renderer = TemplateRenderer()
_vault = CredentialVault()


def _db() -> DatabaseManager:
    return DatabaseManager(DB_PATH)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class DeviceSummary(BaseModel):
    id:            int
    hostname:      str
    management_ip: Optional[str]
    vendor:        Optional[str]
    ssh_user:      Optional[str]
    ssh_port:      int
    status:        str


class GenerateResponse(BaseModel):
    config:   str
    filename: str   # suggested download filename


class ZtpPublishRequest(BaseModel):
    device_ids: list[int]
    credential_device_id: Optional[int] = None
    credential_label: str = "ZTP_SERVER_SSH"
    config_overrides: Dict[str, str] = Field(default_factory=dict)
    target_server_ip: Optional[str] = None
    target_server_port: int = 22
    restart_services: bool = True
    reset_leases: bool = True
    dry_run: bool = False
    requested_by: str = "frontend-user"


class ZtpPublishQueuedResponse(BaseModel):
    job_id: int
    status: str
    total_items: int


class ZtpServerCredentialRequest(BaseModel):
    username: str
    password: str
    label: str = "ZTP_SERVER_SSH"
    device_id: Optional[int] = None


class ZtpServerConnectionTestRequest(BaseModel):
    target_server_ip: Optional[str] = None
    target_server_port: int = 22
    credential_label: str = "ZTP_SERVER_SSH"
    credential_device_id: Optional[int] = None


# ---------------------------------------------------------------------------
# GET /onboarding/devices  — list all inventory devices for the picker
# ---------------------------------------------------------------------------

@router.get("/devices", response_model=list[DeviceSummary])
async def list_devices():
    """Return only 'registered' devices (pushed to NetBox, ready for Day-0)."""
    devices = _db().get_all_devices()
    return [
        DeviceSummary(
            id            = d.id,
            hostname      = d.hostname,
            management_ip = d.management_ip,
            vendor        = d.vendor or "cisco_ios",
            ssh_user      = d.ssh_user,
            ssh_port      = d.ssh_port or 22,
            status        = d.status,
        )
        for d in devices
        if d.status == "planned"
    ]


# ---------------------------------------------------------------------------
# POST /onboarding/generate/{device_id}  — build vendor config from inventory
# ---------------------------------------------------------------------------

@router.post("/generate/{device_id}", response_model=GenerateResponse)
async def generate_for_device(device_id: int):
    """
    Look up a planned device and return its Day-0 bootstrap config.
    The device must already have management_ip, vendor, ssh_user, ssh_password
    set in inventory for a full config to be generated.
    """
    device = _db().get_device_by_id(device_id)
    if not device:
        raise HTTPException(404, f"Device {device_id} not found in inventory")

    # Merge inherited common services defaults (global -> site -> role -> device).
    effective_common = get_effective_profile(
        DB_PATH,
        role=device.role,
        device_id=device.id,
    )

    # Build params dict from the device record.
    raw_params = {
        "hostname":       device.hostname,
        "mgmt_ip":        device.management_ip or "0.0.0.0",
        "subnet_mask":    device.management_subnet_mask or "255.255.255.0",
        "gateway":        _guess_gateway(device.management_ip),
        "mgmt_interface": (device.management_interface or _default_mgmt_iface(device.vendor, device.role)),
        "vendor":         device.vendor or "cisco_ios",
        "role":           device.role or "network-device",
        "ssh_user":       device.ssh_user or "admin",
        "ssh_password":   device.ssh_password or "CHANGE_ME",
        "port":           device.ssh_port or 22,
        **_common_services_template_params(effective_common),
    }
    params = _normalize_day0_params(raw_params)

    config = _render_day0_config(params)
    filename = f"{device.hostname}_day0_config.txt"

    logger.info("Day-0 config generated for device '%s'", device.hostname)

    return GenerateResponse(config=config, filename=filename)


# ---------------------------------------------------------------------------
# POST /onboarding/generate  — generate from manual params (no device lookup)
# ---------------------------------------------------------------------------

class ManualParams(BaseModel):
    hostname:       str
    mgmt_ip:        str
    subnet_mask:    str = "255.255.255.0"
    gateway:        str
    mgmt_interface: str = "GigabitEthernet0/0"
    vendor:         str = "cisco_ios"
    ssh_user:       str = "admin"
    ssh_password:   str = "CHANGE_ME"
    port:           int = 22


@router.post("/generate", response_model=GenerateResponse)
async def generate_manual(params: ManualParams):
    """Generate a Day-0 config from manually supplied params (no device lookup)."""
    normalized = _normalize_day0_params(params.model_dump())
    config = _render_day0_config(normalized)
    filename = f"{params.hostname}_day0_config.txt"
    return GenerateResponse(config=config, filename=filename)


@router.post("/ztp/publish", response_model=ZtpPublishQueuedResponse)
async def publish_to_ztp(body: ZtpPublishRequest, background_tasks: BackgroundTasks):
    requested_ids = [int(device_id) for device_id in body.device_ids if int(device_id) > 0]
    if not requested_ids:
        raise HTTPException(status_code=422, detail="device_ids must include at least one device")

    # Keep order stable but avoid duplicate items in the same job.
    seen: set[int] = set()
    device_ids: list[int] = []
    for device_id in requested_ids:
        if device_id in seen:
            continue
        seen.add(device_id)
        device_ids.append(device_id)

    db = _db()
    missing = [device_id for device_id in device_ids if db.get_device_by_id(device_id) is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"Devices not found: {missing}")

    mode = "single" if len(device_ids) == 1 else "batch"
    target_server_ip = (body.target_server_ip or "192.168.1.240").strip() or "192.168.1.240"

    payload = body.model_dump()
    payload["device_ids"] = device_ids
    payload["target_server_ip"] = target_server_ip

    job_id = await run_in_threadpool(
        create_publish_job,
        db,
        device_ids=device_ids,
        payload=payload,
        requested_by=str(body.requested_by or "frontend-user"),
        mode=mode,
        target_server_ip=target_server_ip,
        credential_label=str(body.credential_label or "ZTP_SERVER_SSH"),
    )

    background_tasks.add_task(run_publish_job, DB_PATH, job_id)
    logger.info("Queued onboarding ZTP publish job %d (mode=%s, items=%d)", job_id, mode, len(device_ids))

    return ZtpPublishQueuedResponse(job_id=job_id, status="queued", total_items=len(device_ids))


@router.get("/ztp/server-credential")
async def get_ztp_server_credential_status(
    label: str = "ZTP_SERVER_SSH",
    device_id: Optional[int] = None,
    user=Depends(require_any),
):
    normalized_device_id = _normalize_credential_device_id(device_id)
    credential = _vault.get(device_id=normalized_device_id, label=label)
    configured = credential is not None
    return {
        "configured": configured,
        "device_id": normalized_device_id,
        "label": label,
        "username": str(credential.get("username") or "") if credential else None,
    }


@router.post("/ztp/server-credential")
async def set_ztp_server_credential(body: ZtpServerCredentialRequest, user=Depends(require_admin)):
    username = str(body.username or "").strip()
    password = str(body.password or "")
    if not username:
        raise HTTPException(status_code=422, detail="username is required")
    if not password:
        raise HTTPException(status_code=422, detail="password is required")

    normalized_device_id = _normalize_credential_device_id(body.device_id)

    try:
        row_id = _vault.store(
            str(body.label or "ZTP_SERVER_SSH"),
            normalized_device_id,
            username,
            password,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=422, detail="device_id does not exist in inventory")
    return {
        "id": row_id,
        "device_id": normalized_device_id,
        "label": str(body.label or "ZTP_SERVER_SSH"),
        "stored": True,
    }


@router.post("/ztp/server-credential/test")
async def test_ztp_server_connection(body: ZtpServerConnectionTestRequest, user=Depends(require_any)):
    target_server_ip = (body.target_server_ip or "192.168.1.240").strip() or "192.168.1.240"
    target_server_port = int(body.target_server_port or 22)
    requested_label = str(body.credential_label or "ZTP_SERVER_SSH").strip() or "ZTP_SERVER_SSH"

    credential_device_id = _normalize_credential_device_id(body.credential_device_id)
    if credential_device_id is None:
        target_device = _db().get_device_by_management_ip(target_server_ip)
        if target_device:
            credential_device_id = target_device.id

    credential, resolved = _resolve_server_credential(
        credential_device_id=credential_device_id,
        requested_label=requested_label,
    )
    if not credential:
        raise HTTPException(
            status_code=404,
            detail=(
                "Server credential not found in vault. "
                f"Tried: {resolved.get('attempted')}. "
                "Store credential first or adjust target/label."
            ),
        )

    start = time.perf_counter()
    probe = await run_in_threadpool(
        _probe_server,
        target_server_ip,
        target_server_port,
        str(credential.get("username") or ""),
        str(credential.get("password") or ""),
    )
    duration_ms = int((time.perf_counter() - start) * 1000)

    return {
        "ok": bool(probe.get("ok")),
        "server_ip": target_server_ip,
        "server_port": target_server_port,
        "duration_ms": duration_ms,
        "credential_label": resolved.get("label"),
        "credential_device_id": resolved.get("device_id"),
        "stdout": str(probe.get("stdout") or "").strip(),
        "stderr": str(probe.get("stderr") or "").strip(),
        "message": "Connected" if probe.get("ok") else "Connection test failed",
    }


@router.get("/ztp/publish")
async def list_ztp_publish_jobs(limit: int = 20):
    db = _db()
    jobs = await run_in_threadpool(list_publish_jobs, db, max(1, min(int(limit), 200)))
    return {"items": jobs, "total": len(jobs)}


@router.get("/ztp/publish/{job_id}")
async def get_ztp_publish_job(job_id: int):
    db = _db()
    payload = await run_in_threadpool(get_publish_job, db, int(job_id))
    if not payload:
        raise HTTPException(status_code=404, detail=f"ZTP publish job {job_id} not found")
    return payload


@router.get("/ztp/publish/{job_id}/manifest")
async def get_ztp_publish_job_manifest(job_id: int):
    db = _db()
    manifest = await run_in_threadpool(get_publish_manifest, db, int(job_id))
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"ZTP publish job {job_id} not found")
    return manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guess_gateway(mgmt_ip: Optional[str]) -> str:
    """Derive a likely gateway from a /24 management IP (last octet → .1)."""
    if not mgmt_ip:
        return "0.0.0.0"
    try:
        parts = mgmt_ip.split(".")
        return f"{parts[0]}.{parts[1]}.{parts[2]}.1"
    except Exception:
        return "0.0.0.0"


def _default_mgmt_iface(vendor: Optional[str], role: Optional[str] = None) -> str:
    vendor = (vendor or "cisco_ios").lower()
    role_norm = (role or "").strip().lower()

    # Cisco distribution/access in this project commonly use L3-routed mgmt ports.
    if "cisco" in vendor and role_norm in ("distribution", "access"):
        return "Ethernet0/0"
    if "huawei" in vendor:
        return "MEth0/0/0"
    if "juniper" in vendor:
        return "fxp0"
    return "GigabitEthernet0/0"


def _normalize_day0_params(params: dict) -> dict:
    normalized = dict(params)
    normalized["vendor"] = (normalized.get("vendor") or "cisco_ios").lower()
    normalized["role"] = str(normalized.get("role") or "network-device").strip().lower()
    normalized.setdefault("subnet_mask", "255.255.255.0")
    # Cisco distribution/access profiles in this project expect routed mgmt interfaces.
    normalized["mgmt_l3_interface"] = bool(
        "cisco" in normalized["vendor"] and normalized["role"] in ("distribution", "access")
    )
    normalized["mgmt_prefix_len"] = _prefix_from_mask(str(normalized.get("subnet_mask") or "255.255.255.0"))
    return normalized


def _common_services_template_params(profile: dict) -> dict:
    return {
        "snmp_community": profile.get("snmp_community"),
        "snmp_location": profile.get("snmp_location"),
        "snmp_contact": profile.get("snmp_contact"),
        "snmp_host_ip": profile.get("snmp_host_ip"),
        "syslog_host_ip": profile.get("syslog_host_ip"),
        "ntp_server_ip": profile.get("ntp_server_ip"),
    }


def _render_day0_config(params: dict) -> str:
    vendor = str(params.get("vendor") or "cisco_ios").lower()
    try:
        return _renderer.render(vendor, "day-0", params)
    except Exception as exc:
        logger.warning(
            "Template day-0 render failed for vendor '%s'; using legacy fallback. reason=%s",
            vendor,
            exc,
        )
        return OnboardingConfigGenerator().generate(params)


def _prefix_from_mask(mask: str) -> int:
    try:
        return sum(bin(int(octet)).count("1") for octet in mask.split("."))
    except Exception:
        return 24


def _resolve_server_credential(
    *,
    credential_device_id: Optional[int],
    requested_label: str,
) -> tuple[Optional[Dict[str, str]], Dict[str, object]]:
    candidate_pairs: list[tuple[Optional[int], str]] = []
    if credential_device_id is not None:
        candidate_pairs.append((int(credential_device_id), requested_label))
        if requested_label != "SSH":
            candidate_pairs.append((int(credential_device_id), "SSH"))

    candidate_pairs.append((None, requested_label))
    if requested_label != "SSH":
        candidate_pairs.append((None, "SSH"))

    attempted: list[str] = []
    seen: set[tuple[Optional[int], str]] = set()
    for device_id, label in candidate_pairs:
        key = (int(device_id), str(label)) if device_id is not None else (None, str(label))
        if key in seen:
            continue
        seen.add(key)
        attempted.append(f"device_id={key[0] if key[0] is not None else 'NULL'} label={key[1]}")
        found = _vault.get(key[0], key[1])
        if found:
            return found, {
                "device_id": key[0],
                "label": key[1],
                "attempted": "; ".join(attempted),
            }

    return None, {"attempted": "; ".join(attempted)}


def _probe_server(host: str, port: int, username: str, password: str) -> Dict[str, object]:
    executor = RemoteLinuxExecutor(host=host, port=int(port), username=username, password=password)
    try:
        executor.connect()
        result = executor.run("echo ZTP_CONNECTION_OK", timeout=10)
        return {
            "ok": bool(result.get("ok")),
            "stdout": str(result.get("stdout") or ""),
            "stderr": str(result.get("stderr") or ""),
        }
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}
    finally:
        try:
            executor.close()
        except Exception:
            pass


def _normalize_credential_device_id(device_id: Optional[int]) -> Optional[int]:
    if device_id is None:
        return None
    try:
        parsed = int(device_id)
    except Exception:
        return None
    if parsed <= 0:
        return None
    return parsed
