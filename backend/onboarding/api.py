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

from .config_generator import OnboardingConfigGenerator
from db.manager import DatabaseManager

logger = logging.getLogger("onboarding.api")

router = APIRouter(prefix="/onboarding", tags=["Onboarding (M0)"])

DB_PATH = "db/netauto.db"


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

    # Build params dict from the device record
    # Use sensible defaults for fields not stored in inventory
    params = {
        "hostname":       device.hostname,
        "mgmt_ip":        device.management_ip or "0.0.0.0",
        "subnet_mask":    "255.255.255.0",
        "gateway":        _guess_gateway(device.management_ip),
        "mgmt_interface": _default_mgmt_iface(device.vendor),
        "vendor":         device.vendor or "cisco_ios",
        "ssh_user":       device.ssh_user or "admin",
        "ssh_password":   device.ssh_password or "CHANGE_ME",
        "port":           device.ssh_port or 22,
    }

    config = OnboardingConfigGenerator().generate(params)
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
    config = OnboardingConfigGenerator().generate(params.model_dump())
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


def _default_mgmt_iface(vendor: Optional[str]) -> str:
    vendor = (vendor or "cisco_ios").lower()
    if "huawei" in vendor:
        return "GigabitEthernet0/0/0"
    if "juniper" in vendor:
        return "fxp0"
    return "GigabitEthernet0/0"
