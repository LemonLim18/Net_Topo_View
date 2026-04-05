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
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common_services.service import get_effective_profile
from config_engine.renderer import TemplateRenderer
from .config_generator import OnboardingConfigGenerator
from db.manager import DatabaseManager

logger = logging.getLogger("onboarding.api")

router = APIRouter(prefix="/onboarding", tags=["Onboarding (M0)"])

DB_PATH = "db/netauto.db"
_renderer = TemplateRenderer()


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
