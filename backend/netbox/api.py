from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from starlette.concurrency import run_in_threadpool
from db.manager import DatabaseManager
from db.models import Device
from netbox.sync_devices import DeviceSyncer
from netbox.sync_topology import TopologySyncer
from netbox.client import NetBoxClient
import logging
import sqlite3

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
    status: str = "planned"
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

class InterfaceCreate(BaseModel):
    name: str
    ip_address: Optional[str] = None
    subnet_mask: Optional[str] = None
    mac_address: Optional[str] = None
    speed: Optional[str] = None
    status: str = "unknown"

class InterfaceUpdate(BaseModel):
    name: Optional[str] = None
    ip_address: Optional[str] = None
    subnet_mask: Optional[str] = None
    mac_address: Optional[str] = None
    speed: Optional[str] = None
    status: Optional[str] = None

class LinkCreate(BaseModel):
    src_device_id: int
    src_interface_id: Optional[int] = None
    dst_device_id: int
    dst_interface_id: Optional[int] = None
    discovery_protocol: str = "manual"


# ---------------------------------------------------------------------------
#  Helper — device dict builder
# ---------------------------------------------------------------------------

def _device_dict(d: Device) -> dict:
    return {
        "id": d.id,
        "hostname": d.hostname,
        "management_ip": d.management_ip,
        "vendor": d.vendor,
        "model": d.model,
        "os_version": d.os_version,
        "serial_number": d.serial_number,
        "status": d.status,
        "source": d.source,
        "last_polled": d.last_polled,
        "ssh_user": d.ssh_user,
        "ssh_port": d.ssh_port,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


# ---------------------------------------------------------------------------
#  Device CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("/devices")
async def list_devices():
    """Return all devices from the local inventory."""
    db = _db()
    devices = db.get_all_devices()
    return {"devices": [_device_dict(d) for d in devices]}


@router.get("/devices/{device_id}")
async def get_device(device_id: int):
    """Get a single device by ID."""
    db = _db()
    d = db.get_device_by_id(device_id)
    if not d:
        raise HTTPException(404, "Device not found")
    return _device_dict(d)


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
        source="manual",
        ssh_user=body.ssh_user,
        ssh_password=body.ssh_password,
        ssh_port=body.ssh_port,
    )
    device_id = db.upsert_device(device)
    created = db.get_device_by_id(device_id)
    return _device_dict(created)


@router.put("/devices/{device_id}")
async def update_device(device_id: int, body: DeviceUpdate):
    """Update an existing device."""
    db = _db()
    existing = db.get_device_by_id(device_id)
    if not existing:
        raise HTTPException(404, "Device not found")

    device = Device(
        id=device_id,
        hostname=body.hostname if body.hostname is not None else existing.hostname,
        management_ip=body.management_ip if body.management_ip is not None else existing.management_ip,
        vendor=body.vendor if body.vendor is not None else existing.vendor,
        model=body.model if body.model is not None else existing.model,
        os_version=body.os_version if body.os_version is not None else existing.os_version,
        serial_number=body.serial_number if body.serial_number is not None else existing.serial_number,
        status=body.status if body.status is not None else existing.status,
        source=existing.source,
        last_polled=existing.last_polled,
        ssh_user=body.ssh_user if body.ssh_user is not None else existing.ssh_user,
        ssh_password=body.ssh_password if body.ssh_password is not None else existing.ssh_password,
        ssh_port=body.ssh_port if body.ssh_port is not None else existing.ssh_port,
    )
    db.upsert_device(device)
    updated = db.get_device_by_id(device_id)
    return _device_dict(updated)


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
#  Interface CRUD endpoints
# ---------------------------------------------------------------------------

def _raw_conn():
    """Get a raw sqlite3 connection for direct queries."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

@router.get("/devices/{device_id}/interfaces")
async def list_interfaces(device_id: int):
    """List all interfaces for a device."""
    db = _db()
    d = db.get_device_by_id(device_id)
    if not d:
        raise HTTPException(404, "Device not found")

    conn = _raw_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM interfaces WHERE device_id = ? ORDER BY name",
            (device_id,)
        ).fetchall()
        return {"interfaces": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.post("/devices/{device_id}/interfaces")
async def create_interface(device_id: int, body: InterfaceCreate):
    """Add an interface to a device."""
    db = _db()
    d = db.get_device_by_id(device_id)
    if not d:
        raise HTTPException(404, "Device not found")

    conn = _raw_conn()
    try:
        cur = conn.execute(
            """INSERT INTO interfaces (device_id, name, ip_address, subnet_mask, mac_address, speed, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device_id, body.name, body.ip_address, body.subnet_mask,
             body.mac_address, body.speed, body.status),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Interface '{body.name}' already exists on this device")
    finally:
        conn.close()


@router.put("/devices/{device_id}/interfaces/{iface_id}")
async def update_interface(device_id: int, iface_id: int, body: InterfaceUpdate):
    """Update an interface."""
    conn = _raw_conn()
    try:
        existing = conn.execute(
            "SELECT * FROM interfaces WHERE id = ? AND device_id = ?",
            (iface_id, device_id)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Interface not found")

        conn.execute(
            """UPDATE interfaces
               SET name = ?, ip_address = ?, subnet_mask = ?, mac_address = ?,
                   speed = ?, status = ?, updated_at = datetime('now')
             WHERE id = ?""",
            (
                body.name if body.name is not None else existing["name"],
                body.ip_address if body.ip_address is not None else existing["ip_address"],
                body.subnet_mask if body.subnet_mask is not None else existing["subnet_mask"],
                body.mac_address if body.mac_address is not None else existing["mac_address"],
                body.speed if body.speed is not None else existing["speed"],
                body.status if body.status is not None else existing["status"],
                iface_id,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iface_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/devices/{device_id}/interfaces/{iface_id}")
async def delete_interface(device_id: int, iface_id: int):
    """Remove an interface."""
    conn = _raw_conn()
    try:
        existing = conn.execute(
            "SELECT * FROM interfaces WHERE id = ? AND device_id = ?",
            (iface_id, device_id)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Interface not found")
        conn.execute("DELETE FROM interfaces WHERE id = ?", (iface_id,))
        conn.commit()
        return {"deleted": True, "id": iface_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  Link CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("/links")
async def list_links():
    """List all links with device/interface names resolved."""
    conn = _raw_conn()
    try:
        rows = conn.execute("""
            SELECT l.id, l.src_device_id, l.dst_device_id,
                   l.src_interface_id, l.dst_interface_id,
                   l.discovery_protocol, l.created_at,
                   sd.hostname AS src_hostname,
                   dd.hostname AS dst_hostname,
                   si.name AS src_interface_name,
                   di.name AS dst_interface_name
            FROM links l
            JOIN devices sd ON l.src_device_id = sd.id
            JOIN devices dd ON l.dst_device_id = dd.id
            LEFT JOIN interfaces si ON l.src_interface_id = si.id
            LEFT JOIN interfaces di ON l.dst_interface_id = di.id
            ORDER BY sd.hostname, si.name
        """).fetchall()
        return {"links": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.post("/links")
async def create_link(body: LinkCreate):
    """Create a link between two device interfaces."""
    conn = _raw_conn()
    try:
        cur = conn.execute(
            """INSERT INTO links (src_device_id, src_interface_id, dst_device_id, dst_interface_id,
                                  discovery_protocol)
               VALUES (?, ?, ?, ?, ?)""",
            (body.src_device_id, body.src_interface_id,
             body.dst_device_id, body.dst_interface_id,
             body.discovery_protocol),
        )
        conn.commit()
        return {"id": cur.lastrowid, "created": True}
    except sqlite3.IntegrityError as e:
        raise HTTPException(409, f"Link already exists or FK violation: {e}")
    finally:
        conn.close()


@router.delete("/links/{link_id}")
async def delete_link(link_id: int):
    """Remove a link."""
    conn = _raw_conn()
    try:
        existing = conn.execute("SELECT id FROM links WHERE id = ?", (link_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Link not found")
        conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
        conn.commit()
        return {"deleted": True, "id": link_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  Push to NetBox
# ---------------------------------------------------------------------------

@router.post("/devices/{device_id}/push")
async def push_device_to_netbox(device_id: int):
    """Push a single device to NetBox and mark it as 'registered'."""
    db = _db()
    d = db.get_device_by_id(device_id)
    if not d:
        raise HTTPException(404, "Device not found")

    # Push to NetBox
    try:
        nb = NetBoxClient.get_client()
        if nb and nb.api:
            vendor_to_platform = {
                "cisco_ios": "cisco-ios",
                "cisco_xe": "cisco-xe",
                "huawei_vrp": "huawei-vrp",
                "juniper_junos": "juniper-junos",
            }
            nb.create_device(
                name=d.hostname,
                mgmt_ip=d.management_ip,
                platform_slug=vendor_to_platform.get(d.vendor, d.vendor),
            )
            logger.info("Pushed device '%s' to NetBox", d.hostname)
        else:
            logger.warning("NetBox not connected — marking device as registered locally only")
    except Exception as e:
        logger.warning("NetBox push for '%s' failed: %s — still marking as registered", d.hostname, e)

    # Update status to registered
    device = Device(
        hostname=d.hostname,
        management_ip=d.management_ip,
        vendor=d.vendor,
        model=d.model,
        os_version=d.os_version,
        serial_number=d.serial_number,
        status="registered",
        source=d.source if d.source != "crawler" else "manual",
        last_polled=d.last_polled,
        ssh_user=d.ssh_user,
        ssh_password=d.ssh_password,
        ssh_port=d.ssh_port,
    )
    db.upsert_device(device)
    updated = db.get_device_by_id(device_id)
    return {"pushed": True, **_device_dict(updated)}


@router.post("/push-all")
async def push_all_to_netbox():
    """Push all 'planned' devices to NetBox in batch."""
    db = _db()
    devices = db.get_all_devices()
    planned = [d for d in devices if d.status == "planned"]

    if not planned:
        return {"pushed_count": 0, "message": "No planned devices to push"}

    pushed = 0
    for d in planned:
        try:
            nb = NetBoxClient.get_client()
            if nb and nb.api:
                vendor_to_platform = {
                    "cisco_ios": "cisco-ios",
                    "cisco_xe": "cisco-xe",
                    "huawei_vrp": "huawei-vrp",
                    "juniper_junos": "juniper-junos",
                }
                nb.create_device(
                    name=d.hostname,
                    mgmt_ip=d.management_ip,
                    platform_slug=vendor_to_platform.get(d.vendor, d.vendor),
                )
        except Exception as e:
            logger.warning("NetBox push for '%s' failed: %s", d.hostname, e)

        device = Device(
            hostname=d.hostname,
            management_ip=d.management_ip,
            vendor=d.vendor,
            model=d.model,
            os_version=d.os_version,
            serial_number=d.serial_number,
            status="registered",
            source=d.source,
            last_polled=d.last_polled,
            ssh_user=d.ssh_user,
            ssh_password=d.ssh_password,
            ssh_port=d.ssh_port,
        )
        db.upsert_device(device)
        pushed += 1

    return {"pushed_count": pushed, "message": f"Pushed {pushed} devices to NetBox"}


# ---------------------------------------------------------------------------
#  NetBox sync endpoints
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
