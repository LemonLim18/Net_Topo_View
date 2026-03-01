from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional
from starlette.concurrency import run_in_threadpool
from db.manager import DatabaseManager
from db.models import Device
from netbox.sync_devices import DeviceSyncer
from netbox.sync_topology import TopologySyncer
from netbox.client import NetBoxClient
import logging

logger = logging.getLogger("netbox.api")

router = APIRouter()

DB_PATH = "db/netauto.db"

def _db() -> DatabaseManager:
    return DatabaseManager(DB_PATH)


# ---------------------------------------------------------------------------
#  Pydantic schemas
# ---------------------------------------------------------------------------

class DeviceCreate(BaseModel):
    hostname: str
    management_ip: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    os_version: Optional[str] = None
    serial_number: Optional[str] = None
    status: str = "unknown"
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_port: int = 22

class DeviceUpdate(BaseModel):
    hostname: Optional[str] = None
    management_ip: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    os_version: Optional[str] = None
    serial_number: Optional[str] = None
    status: Optional[str] = None
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_port: Optional[int] = None


# ---------------------------------------------------------------------------
#  Device CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("/devices")
async def list_devices():
    """Return all devices from the local inventory."""
    db = _db()
    devices = db.get_all_devices()
    return {
        "devices": [
            {
                "id": d.id,
                "hostname": d.hostname,
                "management_ip": d.management_ip,
                "vendor": d.vendor,
                "model": d.model,
                "os_version": d.os_version,
                "serial_number": d.serial_number,
                "status": d.status,
                "last_polled": d.last_polled,
                "ssh_user": d.ssh_user,
                "ssh_port": d.ssh_port,
                "created_at": d.created_at,
                "updated_at": d.updated_at,
            }
            for d in devices
        ]
    }


@router.get("/devices/{device_id}")
async def get_device(device_id: int):
    """Get a single device by ID."""
    db = _db()
    d = db.get_device_by_id(device_id)
    if not d:
        raise HTTPException(404, "Device not found")
    return {
        "id": d.id,
        "hostname": d.hostname,
        "management_ip": d.management_ip,
        "vendor": d.vendor,
        "model": d.model,
        "os_version": d.os_version,
        "serial_number": d.serial_number,
        "status": d.status,
        "last_polled": d.last_polled,
        "ssh_user": d.ssh_user,
        "ssh_port": d.ssh_port,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


@router.post("/devices")
async def create_device(body: DeviceCreate, background_tasks: BackgroundTasks):
    """Create a new device in the local inventory.

    Best-effort push to NetBox is attempted if the client is available.
    """
    db = _db()

    # Check for duplicate hostname
    existing = db.get_device_by_hostname(body.hostname)
    if existing:
        raise HTTPException(409, f"Device with hostname '{body.hostname}' already exists (id={existing.id})")

    device = Device(
        hostname=body.hostname,
        management_ip=body.management_ip,
        vendor=body.vendor,
        model=body.model,
        os_version=body.os_version,
        serial_number=body.serial_number,
        status=body.status,
        ssh_user=body.ssh_user,
        ssh_password=body.ssh_password,
        ssh_port=body.ssh_port,
    )
    device_id = db.upsert_device(device)

    # Best-effort push to NetBox — run in background so it never blocks the response
    background_tasks.add_task(_try_push_to_netbox, body, "create")

    created = db.get_device_by_id(device_id)
    return {
        "id": created.id,
        "hostname": created.hostname,
        "management_ip": created.management_ip,
        "vendor": created.vendor,
        "model": created.model,
        "os_version": created.os_version,
        "serial_number": created.serial_number,
        "status": created.status,
        "ssh_user": created.ssh_user,
        "ssh_port": created.ssh_port,
        "created_at": created.created_at,
        "updated_at": created.updated_at,
    }


@router.put("/devices/{device_id}")
async def update_device(device_id: int, body: DeviceUpdate):
    """Update an existing device."""
    db = _db()
    existing = db.get_device_by_id(device_id)
    if not existing:
        raise HTTPException(404, "Device not found")

    # Merge incoming fields over existing values
    device = Device(
        id=device_id,
        hostname=body.hostname if body.hostname is not None else existing.hostname,
        management_ip=body.management_ip if body.management_ip is not None else existing.management_ip,
        vendor=body.vendor if body.vendor is not None else existing.vendor,
        model=body.model if body.model is not None else existing.model,
        os_version=body.os_version if body.os_version is not None else existing.os_version,
        serial_number=body.serial_number if body.serial_number is not None else existing.serial_number,
        status=body.status if body.status is not None else existing.status,
        last_polled=existing.last_polled,
        ssh_user=body.ssh_user if body.ssh_user is not None else existing.ssh_user,
        ssh_password=body.ssh_password if body.ssh_password is not None else existing.ssh_password,
        ssh_port=body.ssh_port if body.ssh_port is not None else existing.ssh_port,
    )
    db.upsert_device(device)

    updated = db.get_device_by_id(device_id)
    return {
        "id": updated.id,
        "hostname": updated.hostname,
        "management_ip": updated.management_ip,
        "vendor": updated.vendor,
        "model": updated.model,
        "os_version": updated.os_version,
        "serial_number": updated.serial_number,
        "status": updated.status,
        "last_polled": updated.last_polled,
        "ssh_user": updated.ssh_user,
        "ssh_port": updated.ssh_port,
        "created_at": updated.created_at,
        "updated_at": updated.updated_at,
    }


@router.delete("/devices/{device_id}")
async def delete_device(device_id: int):
    """Delete a device from the local inventory."""
    db = _db()
    existing = db.get_device_by_id(device_id)
    if not existing:
        raise HTTPException(404, "Device not found")
    db.delete_device(device_id)
    return {"deleted": True, "id": device_id, "hostname": existing.hostname}


# ---------------------------------------------------------------------------
#  NetBox sync endpoints (existing, fixed DB path)
# ---------------------------------------------------------------------------

@router.post("/sync/devices")
async def sync_devices(background_tasks: BackgroundTasks):
    """
    Triggers a manual sync of all devices from NetBox.
    Runs in a background task so it never blocks the event loop.
    """
    def _run():
        db = DatabaseManager(DB_PATH)
        syncer = DeviceSyncer(db)
        syncer.sync_all_devices()

    background_tasks.add_task(_run)
    return {"status": "accepted", "message": "NetBox device sync started in background"}

@router.post("/sync/topology")
async def sync_topology(background_tasks: BackgroundTasks):
    """
    Triggers a manual sync of topology (cables) from NetBox.
    Runs in a background task so it never blocks the event loop.
    """
    def _run():
        db = DatabaseManager(DB_PATH)
        syncer = TopologySyncer(db)
        syncer.sync_connections()

    background_tasks.add_task(_run)
    return {"status": "accepted", "message": "NetBox topology sync started in background"}


# ---------------------------------------------------------------------------
#  Phase 4 — Planned Topology from NetBox (UC15 / UC17)
# ---------------------------------------------------------------------------

@router.get("/topology/planned")
async def get_planned_topology():
    """
    Build the design-of-record (planned) topology graph from NetBox.

    Returns the same node/edge structure as the BFS crawler output so the
    frontend topology renderer can display it directly.  Enables visual
    comparison of *planned* vs *discovered* topology.
    """
    db = _db()
    syncer = TopologySyncer(db)
    try:
        planned = await run_in_threadpool(syncer.get_planned_topology)
        return planned
    except Exception as e:
        logger.error("Planned topology fetch failed: %s", e)
        raise HTTPException(500, f"Failed to build planned topology: {e}")


# ---------------------------------------------------------------------------
#  Helper — best-effort NetBox push
# ---------------------------------------------------------------------------

def _try_push_to_netbox(device_data: DeviceCreate, action: str = "create"):
    """Attempt to push a device to NetBox (blocking — call via background task)."""
    try:
        nb = NetBoxClient.get_client()
        if nb and nb.api:
            if action == "create":
                nb.create_device(
                    name=device_data.hostname,
                    mgmt_ip=device_data.management_ip,
                    platform_slug=device_data.vendor,
                )
    except Exception as e:
        logger.warning("Best-effort NetBox push failed (%s): %s", action, e)
