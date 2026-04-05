from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from starlette.concurrency import run_in_threadpool
from db.manager import DatabaseManager
from db.models import Device
from llm.context_contract import CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY
from llm.refresh_fabric import publish_context_refresh_event
from netbox.sync_devices import DeviceSyncer
from netbox.sync_topology import TopologySyncer
from netbox.client import NetBoxClient
import logging
import sqlite3
import ipaddress

logger = logging.getLogger("netbox.api")

router = APIRouter()

DB_PATH = "db/netauto.db"

def _db() -> DatabaseManager:
    return DatabaseManager(DB_PATH)


def _invalidate_inventory_context(source: str, summary: str, metadata: Optional[dict] = None) -> None:
    event_metadata = dict(metadata or {})
    # Device inventory CRUD should invalidate live caches only. It must not
    # trigger any RAG/vector embedding work.
    event_metadata.setdefault("skip_vector_refresh", True)
    publish_context_refresh_event(
        source=source,
        action="inventory_mutation",
        summary=summary,
        domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY),
        metadata=event_metadata,
        refresh_mode="none",
    )


# ---------------------------------------------------------------------------
#  Pydantic schemas
# ---------------------------------------------------------------------------

class DeviceCreate(BaseModel):
    hostname: str
    management_ip: Optional[str] = None
    management_subnet_mask: Optional[str] = None
    management_interface: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    os_version: Optional[str] = None
    serial_number: Optional[str] = None
    status: str = "planned"
    role: str = "network-device"
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_port: int = 22

class DeviceUpdate(BaseModel):
    hostname: Optional[str] = None
    management_ip: Optional[str] = None
    management_subnet_mask: Optional[str] = None
    management_interface: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    os_version: Optional[str] = None
    serial_number: Optional[str] = None
    status: Optional[str] = None
    role: Optional[str] = None
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
    """Device dict for list responses; excludes ssh_password."""
    return {
        "id": d.id,
        "hostname": d.hostname,
        "management_ip": d.management_ip,
        "management_subnet_mask": d.management_subnet_mask,
        "management_interface": d.management_interface,
        "vendor": d.vendor,
        "model": d.model,
        "os_version": d.os_version,
        "serial_number": d.serial_number,
        "status": d.status,
        "role": d.role,
        "source": d.source,
        "last_polled": d.last_polled,
        "ssh_user": d.ssh_user,
        "ssh_port": d.ssh_port,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


def _device_dict_with_credentials(d: Device) -> dict:
    """Device dict including ssh_password for single-device fetch (e.g. SSH form pre-fill)."""
    out = _device_dict(d)
    out["ssh_password"] = d.ssh_password
    return out


def _parse_cidr(ip_str: Optional[str], current_mask: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Auto-split CIDR if provided in the IP string and mask is missing."""
    if not ip_str or "/" not in ip_str:
        return ip_str, current_mask
    try:
        ip, prefix = ip_str.split("/")
        p_int = int(prefix)
        # Convert prefix to mask
        m = (0xffffffff >> (32 - p_int)) << (32 - p_int)
        mask = f"{(m >> 24) & 0xff}.{(m >> 16) & 0xff}.{(m >> 8) & 0xff}.{m & 0xff}"
        return ip, mask if not current_mask else current_mask
    except Exception:
        return ip_str, current_mask


def _normalize_mask_to_prefix(mask_raw: Optional[str]) -> Optional[int]:
    txt = str(mask_raw or "").strip()
    if not txt:
        return None
    if txt.startswith("/"):
        txt = txt[1:]
    if txt.isdigit():
        pfx = int(txt)
        if 0 <= pfx <= 32:
            return pfx
        return None
    try:
        return int(ipaddress.IPv4Network(f"0.0.0.0/{txt}").prefixlen)
    except ValueError:
        return None


def _interface_network_tuple(iface_row: sqlite3.Row) -> tuple[str, Optional[ipaddress.IPv4Network]]:
    """Return inferred layer (l2/l3) and IPv4 network when available."""
    ip_txt = str(iface_row["ip_address"] or "").strip()
    mask_txt = str(iface_row["subnet_mask"] or "").strip()

    if not ip_txt:
        return "l2", None

    # Treat any interface with an IP as routed/L3 and require mask for subnet checks.
    if "/" in ip_txt:
        try:
            iface = ipaddress.IPv4Interface(ip_txt)
            return "l3", iface.network
        except ValueError as exc:
            raise HTTPException(400, f"Invalid interface IP format '{ip_txt}' on {iface_row['name']}: {exc}") from exc

    pfx = _normalize_mask_to_prefix(mask_txt)
    if pfx is None:
        raise HTTPException(
            400,
            (
                f"Interface {iface_row['name']} has IP '{ip_txt}' but missing/invalid subnet mask '{mask_txt}'. "
                "Set a valid subnet mask before creating this link."
            ),
        )
    try:
        iface = ipaddress.IPv4Interface(f"{ip_txt}/{pfx}")
        return "l3", iface.network
    except ValueError as exc:
        raise HTTPException(400, f"Invalid interface IP '{ip_txt}' on {iface_row['name']}: {exc}") from exc


def _validate_link_interfaces_compatible(src_row: sqlite3.Row, dst_row: sqlite3.Row) -> None:
    src_layer, src_net = _interface_network_tuple(src_row)
    dst_layer, dst_net = _interface_network_tuple(dst_row)

    if src_layer != dst_layer:
        raise HTTPException(
            400,
            (
                f"Cannot connect interfaces with different layer types: "
                f"{src_row['name']} is {src_layer.upper()} and {dst_row['name']} is {dst_layer.upper()}."
            ),
        )

    if src_layer == "l3" and src_net is not None and dst_net is not None and src_net != dst_net:
        raise HTTPException(
            400,
            (
                "Cannot connect routed interfaces from different subnets: "
                f"{src_row['name']} is in {src_net} and {dst_row['name']} is in {dst_net}."
            ),
        )


# ---------------------------------------------------------------------------
#  Device CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("/devices")
async def list_devices():
    """Return all devices from the local inventory. Filters out 'crawler' source devices."""
    db = _db()
    devices = db.get_all_devices()
    # M14: Filter out devices that purely came from the crawler discovery (live topology)
    # The inventory should only show authoritative records (manual or NetBox).
    filtered = [d for d in devices if d.source != 'crawler']
    return {"devices": [_device_dict(d) for d in filtered]}


@router.get("/devices/{device_id}")
async def get_device(device_id: int):
    """Get a single device by ID. Includes ssh_password for SSH form pre-fill (authenticated users only)."""
    db = _db()
    d = db.get_device_by_id(device_id)
    if not d:
        raise HTTPException(404, "Device not found")
    return _device_dict_with_credentials(d)


@router.post("/devices")
async def create_device(body: DeviceCreate):
    """Create a new device in the local inventory and sync to NetBox.

    Device metadata (name, mgmt IP, vendor, role, etc.) is pushed to NetBox.
    SSH username/password are stored only in the local DB (NetBox has no
    standard credential fields).
    """
    db = _db()

    # Check for duplicate hostname
    existing = db.get_device_by_hostname(body.hostname)
    if existing:
        raise HTTPException(409, f"Device with hostname '{body.hostname}' already exists (id={existing.id})")

    # Auto-push to NetBox (all manual entries sync immediately)
    status = body.status or "planned"
    nb_id = None
    netbox_synced = False
    netbox_error = None
    try:
        nb = NetBoxClient.get_client()
        if nb and nb.api:
            nb_dev = nb.create_device(
                name=body.hostname,
                mgmt_ip=body.management_ip,
                mgmt_mask=body.management_subnet_mask,
                vendor=body.vendor or "unknown",
                model=body.model,
                role=body.role,
                ssh_user=body.ssh_user,
                ssh_password=body.ssh_password,
                serial=body.serial_number,
                os_version=body.os_version,
            )
            if nb_dev:
                nb_id = getattr(nb_dev, "id", None)
                netbox_synced = True
                logger.info("Auto-pushed device '%s' to NetBox on registration (ID: %s)", body.hostname, nb_id)
        else:
            netbox_error = "NetBox not configured or unavailable"
    except Exception as e:
        logger.warning("Auto-push for '%s' failed: %s — still registering locally", body.hostname, e)
        netbox_error = str(e)

    device = Device(
        hostname=body.hostname,
        management_ip=body.management_ip,
        management_subnet_mask=body.management_subnet_mask,
        management_interface=body.management_interface,
        vendor=body.vendor,
        model=body.model,
        os_version=body.os_version,
        serial_number=body.serial_number,
        status=status,
        role=body.role,
        source="manual",
        netbox_id=nb_id,
        ssh_user=body.ssh_user,
        ssh_password=body.ssh_password,
        ssh_port=body.ssh_port,
    )
    device_id = db.upsert_device(device)
    created = db.get_device_by_id(device_id)
    _invalidate_inventory_context(
        source="netbox_api",
        summary=f"device created: {body.hostname}",
        metadata={"hostname": body.hostname, "operation": "create"},
    )
    out = _device_dict(created)
    out["netbox_synced"] = netbox_synced
    if netbox_error:
        out["netbox_error"] = netbox_error
    return out


@router.put("/devices/{device_id}")
async def update_device(device_id: int, body: DeviceUpdate):
    """Update an existing device in the local DB and sync changes to NetBox.

    Device metadata (name, mgmt IP, vendor, role, etc.) is synced to NetBox.
    SSH username/password are stored only in the local DB.
    """
    db = _db()
    existing = db.get_device_by_id(device_id)
    if not existing:
        raise HTTPException(404, "Device not found")

    new_status = body.status if body.status is not None else existing.status
    hostname = body.hostname if body.hostname is not None else existing.hostname
    mgmt_ip = body.management_ip if body.management_ip is not None else existing.management_ip
    vendor = body.vendor if body.vendor is not None else existing.vendor
    model = body.model if body.model is not None else existing.model
    role = body.role if body.role is not None else existing.role

    # Auto-push or Sync to NetBox
    nb_id = existing.netbox_id
    netbox_synced = False
    netbox_error = None
    try:
        nb = NetBoxClient.get_client()
        if nb and nb.api:
            # We sync to NetBox regardless of status (Planned/Active/Down)
            # to ensure the single source of truth stays updated.
            nb_dev = nb.update_device(
                name=hostname,
                mgmt_ip=mgmt_ip,
                mgmt_mask=body.management_subnet_mask if body.management_subnet_mask is not None else existing.management_subnet_mask,
                vendor=vendor or "unknown",
                model=model,
                role=role,
                original_name=existing.hostname,
                ssh_user=body.ssh_user if body.ssh_user is not None else existing.ssh_user,
                ssh_password=body.ssh_password if body.ssh_password is not None else existing.ssh_password,
                serial=body.serial_number if body.serial_number is not None else existing.serial_number,
                os_version=body.os_version if body.os_version is not None else existing.os_version,
            )
            if nb_dev:
                nb_id = getattr(nb_dev, "id", None) or nb_id
                netbox_synced = True
                logger.info("Synced updates for device '%s' (orig: '%s', ID: %s) to NetBox", hostname, existing.hostname, nb_id)
        else:
            netbox_error = "NetBox not configured or unavailable"
    except Exception as e:
        logger.warning("Auto-sync for '%s' failed: %s — still updating locally", hostname, e)
        netbox_error = str(e)

    device = Device(
        id=device_id,
        hostname=hostname,
        management_ip=mgmt_ip,
        management_subnet_mask=body.management_subnet_mask if body.management_subnet_mask is not None else existing.management_subnet_mask,
        management_interface=body.management_interface if body.management_interface is not None else existing.management_interface,
        vendor=vendor,
        netbox_id=nb_id,
        model=model,
        os_version=body.os_version if body.os_version is not None else existing.os_version,
        serial_number=body.serial_number if body.serial_number is not None else existing.serial_number,
        status=new_status,
        role=role,
        source=existing.source,
        last_polled=existing.last_polled,
        ssh_user=body.ssh_user if body.ssh_user is not None else existing.ssh_user,
        ssh_password=body.ssh_password if body.ssh_password is not None else existing.ssh_password,
        ssh_port=body.ssh_port if body.ssh_port is not None else existing.ssh_port,
    )
    db.upsert_device(device)
    updated = db.get_device_by_id(device_id)
    _invalidate_inventory_context(
        source="netbox_api",
        summary=f"device updated: {hostname}",
        metadata={"hostname": hostname, "operation": "update"},
    )
    out = _device_dict(updated)
    out["netbox_synced"] = netbox_synced
    if netbox_error:
        out["netbox_error"] = netbox_error
    return out


@router.delete("/devices/{device_id}")
async def delete_device(device_id: int):
    """Delete a device from the local inventory and NetBox."""
    db = _db()
    existing = db.get_device_by_id(device_id)
    if not existing:
        raise HTTPException(404, "Device not found")

    # Best-effort delete from NetBox
    try:
        nb = NetBoxClient.get_client()
        if nb and nb.api:
            nb.delete_device_by_name(existing.hostname)
            logger.info("Deleted device '%s' from NetBox", existing.hostname)
    except Exception as e:
        logger.warning("NetBox delete for '%s' failed: %s — still deleting locally", existing.hostname, e)

    db.delete_device(device_id)
    _invalidate_inventory_context(
        source="netbox_api",
        summary=f"device deleted: {existing.hostname}",
        metadata={"hostname": existing.hostname, "operation": "delete"},
    )
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
    """List all interfaces for a device, including connection status."""
    db = _db()
    d = db.get_device_by_id(device_id)
    if not d:
        raise HTTPException(404, "Device not found")

    conn = _raw_conn()
    try:
        # Join with links to find if this interface is connected to another
        rows = conn.execute(
            """
            SELECT i.*, 
                   l.id as link_id,
                   l.src_device_id, l.dst_device_id,
                   l.src_interface_id, l.dst_interface_id,
                   sd.hostname as src_hostname, dd.hostname as dst_hostname,
                   si.name as src_iface_name, di.name as dst_iface_name
            FROM interfaces i
            LEFT JOIN links l ON (i.id = l.src_interface_id OR i.id = l.dst_interface_id)
            LEFT JOIN devices sd ON l.src_device_id = sd.id
            LEFT JOIN devices dd ON l.dst_device_id = dd.id
            LEFT JOIN interfaces si ON l.src_interface_id = si.id
            LEFT JOIN interfaces di ON l.dst_interface_id = di.id
            WHERE i.device_id = ?
            ORDER BY i.name
            """,
            (device_id,)
        ).fetchall()
        
        interfaces = []
        for r in rows:
            iface = dict(r)
            # Build the connected_to object if a link exists
            if r["link_id"]:
                if r["id"] == r["src_interface_id"]:
                    # This is the source side, so connected to destination
                    iface["connected_to"] = {
                        "device": {"id": r["dst_device_id"], "name": r["dst_hostname"]},
                        "interface": {"id": r["dst_interface_id"], "name": r["dst_iface_name"]}
                    }
                else:
                    # This is the destination side, so connected to source
                    iface["connected_to"] = {
                        "device": {"id": r["src_device_id"], "name": r["src_hostname"]},
                        "interface": {"id": r["src_interface_id"], "name": r["src_iface_name"]}
                    }
            else:
                iface["connected_to"] = None
            
            interfaces.append(iface)
            
        return {"interfaces": interfaces}
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
        # Auto-parse CIDR if provided
        ip_addr, sub_mask = _parse_cidr(body.ip_address, body.subnet_mask)
        
        cur = conn.execute(
            """INSERT INTO interfaces (device_id, name, ip_address, subnet_mask, mac_address, speed, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device_id, body.name, ip_addr, sub_mask,
             body.mac_address, body.speed, body.status),
        )
        conn.commit()
        
        # Auto-sync interface and IP to NetBox
        try:
            nb = NetBoxClient.get_client()
            if nb and nb.api:
                if body.ip_address:
                    nb.assign_ip_to_interface(d.hostname, body.name, body.ip_address)
                else:
                    nb.get_or_create_interface(d.hostname, body.name)
        except Exception as e:
            logger.warning("Interface sync to NetBox for '%s:%s' failed: %s", d.hostname, body.name, e)

        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (cur.lastrowid,)).fetchone()
        _invalidate_inventory_context(
            source="netbox_api",
            summary=f"interface created: {d.hostname}:{body.name}",
            metadata={"hostname": d.hostname, "interface": body.name, "operation": "create_interface"},
        )
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Interface '{body.name}' already exists on this device")
    finally:
        conn.close()


@router.put("/devices/{device_id}/interfaces/{iface_id}")
async def update_interface(device_id: int, iface_id: int, body: InterfaceUpdate):
    """Update an interface."""
    db = _db()
    conn = _raw_conn()
    try:
        existing = conn.execute(
            "SELECT * FROM interfaces WHERE id = ? AND device_id = ?",
            (iface_id, device_id)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Interface not found")

        # Auto-parse CIDR if provided in the IP address update
        new_ip = body.ip_address if body.ip_address is not None else existing["ip_address"]
        new_mask = body.subnet_mask if body.subnet_mask is not None else existing["subnet_mask"]
        
        # Only auto-parse if the IP address itself is being changed in this request
        if body.ip_address is not None:
            new_ip, parsed_mask = _parse_cidr(new_ip, body.subnet_mask)
            if parsed_mask:
                new_mask = parsed_mask

        conn.execute(
            """UPDATE interfaces
               SET name = ?, ip_address = ?, subnet_mask = ?, mac_address = ?,
                   speed = ?, status = ?, updated_at = datetime('now')
             WHERE id = ?""",
            (
                body.name if body.name is not None else existing["name"],
                new_ip,
                new_mask,
                body.mac_address if body.mac_address is not None else existing["mac_address"],
                body.speed if body.speed is not None else existing["speed"],
                body.status if body.status is not None else existing["status"],
                iface_id,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iface_id,)).fetchone()
        device = db.get_device_by_id(device_id)
        _invalidate_inventory_context(
            source="netbox_api",
            summary=f"interface updated: {(device.hostname if device else device_id)}:{row['name']}",
            metadata={
                "device_id": device_id,
                "iface_id": iface_id,
                "interface": row["name"],
                "operation": "update_interface",
            },
        )
        return dict(row)
    finally:
        conn.close()


@router.delete("/devices/{device_id}/interfaces/{iface_id}")
async def delete_interface(device_id: int, iface_id: int):
    """Remove an interface from the local inventory and NetBox."""
    db = _db()
    device = db.get_device_by_id(device_id)
    conn = _raw_conn()
    try:
        existing = conn.execute(
            "SELECT * FROM interfaces WHERE id = ? AND device_id = ?",
            (iface_id, device_id)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Interface not found")

        # Best-effort delete from NetBox
        if device:
            try:
                nb = NetBoxClient.get_client()
                if nb and nb.api:
                    nb.delete_interface_by_name(device.hostname, existing["name"])
            except Exception as e:
                logger.warning("NetBox interface delete for '%s:%s' failed: %s", device.hostname, existing["name"], e)

        conn.execute("DELETE FROM interfaces WHERE id = ?", (iface_id,))
        conn.commit()
        _invalidate_inventory_context(
            source="netbox_api",
            summary=f"interface deleted: {(device.hostname if device else device_id)}:{existing['name']}",
            metadata={
                "device_id": device_id,
                "iface_id": iface_id,
                "interface": existing["name"],
                "operation": "delete_interface",
            },
        )
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
        if body.src_device_id == body.dst_device_id:
            raise HTTPException(400, "Source and destination devices must be different")

        if not body.src_interface_id or not body.dst_interface_id:
            raise HTTPException(400, "Both source and destination interfaces are required")

        # Resolve hostnames + interface names before insert so we can validate/sync first.
        db = _db()
        src_dev = db.get_device_by_id(body.src_device_id)
        dst_dev = db.get_device_by_id(body.dst_device_id)
        if not src_dev or not dst_dev:
            raise HTTPException(404, "Source or destination device not found")

        src_row = conn.execute(
            "SELECT id, name, ip_address, subnet_mask FROM interfaces WHERE id = ? AND device_id = ?",
            (body.src_interface_id, body.src_device_id),
        ).fetchone()
        dst_row = conn.execute(
            "SELECT id, name, ip_address, subnet_mask FROM interfaces WHERE id = ? AND device_id = ?",
            (body.dst_interface_id, body.dst_device_id),
        ).fetchone()
        if not src_row or not dst_row:
            raise HTTPException(400, "Source or destination interface is invalid for the selected device")

        _validate_link_interfaces_compatible(src_row, dst_row)

        src_iface_name = src_row["name"]
        dst_iface_name = dst_row["name"]

        # Manual links are strict: NetBox cable creation must succeed.
        if body.discovery_protocol == "manual":
            if not src_dev.netbox_id or not dst_dev.netbox_id:
                raise HTTPException(
                    409,
                    "One or both devices are not synced to NetBox yet; sync the devices first before creating the link",
                )
            nb = NetBoxClient.get_client()
            if not nb or not nb.api:
                raise HTTPException(503, "NetBox connection unavailable; link not created")

            cable = nb.create_cable(
                src_dev.hostname,
                src_iface_name,
                dst_dev.hostname,
                dst_iface_name,
            )
            if cable is None:
                raise HTTPException(
                    502,
                    "NetBox cable sync failed; local link was not created",
                )

        cur = conn.execute(
            """INSERT INTO links (src_device_id, src_interface_id, dst_device_id, dst_interface_id,
                                  discovery_protocol)
               VALUES (?, ?, ?, ?, ?)""",
            (body.src_device_id, body.src_interface_id,
             body.dst_device_id, body.dst_interface_id,
             body.discovery_protocol),
        )
        conn.commit()
        _invalidate_inventory_context(
            source="netbox_api",
            summary=f"link created: {src_dev.hostname}:{src_iface_name} -> {dst_dev.hostname}:{dst_iface_name}",
            metadata={
                "link_id": cur.lastrowid,
                "src_hostname": src_dev.hostname,
                "dst_hostname": dst_dev.hostname,
                "operation": "create_link",
            },
        )
        return {
            "id": cur.lastrowid,
            "created": True,
            "netbox_synced": body.discovery_protocol == "manual",
        }
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise HTTPException(409, f"Link already exists or FK violation: {e}")
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("Unexpected error creating link: %s", e)
        raise HTTPException(500, "Unexpected error creating link")
    finally:
        conn.close()


@router.delete("/links/{link_id}")
async def delete_link(link_id: int):
    """Remove a link."""
    conn = _raw_conn()
    try:
        existing = conn.execute(
            """
            SELECT l.id, sd.hostname AS src_hostname, dd.hostname AS dst_hostname,
                   si.name AS src_interface_name, di.name AS dst_interface_name
            FROM links l
            JOIN devices sd ON l.src_device_id = sd.id
            JOIN devices dd ON l.dst_device_id = dd.id
            LEFT JOIN interfaces si ON l.src_interface_id = si.id
            LEFT JOIN interfaces di ON l.dst_interface_id = di.id
            WHERE l.id = ?
            """,
            (link_id,),
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Link not found")
        conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
        conn.commit()
        _invalidate_inventory_context(
            source="netbox_api",
            summary=(
                f"link deleted: {existing['src_hostname']}:{existing['src_interface_name']} -> "
                f"{existing['dst_hostname']}:{existing['dst_interface_name']}"
            ),
            metadata={
                "link_id": link_id,
                "src_hostname": existing["src_hostname"],
                "dst_hostname": existing["dst_hostname"],
                "operation": "delete_link",
            },
        )
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

    # Push to NetBox (auto-creates prerequisites)
    try:
        nb = NetBoxClient.get_client()
        if nb and nb.api:
            nb.create_device(
                name=d.hostname,
                mgmt_ip=d.management_ip,
                vendor=d.vendor or "cisco_ios",
                model=d.model,
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
    _invalidate_inventory_context(
        source="netbox_api",
        summary=f"device pushed to NetBox: {d.hostname}",
        metadata={"hostname": d.hostname, "operation": "push_device"},
    )
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
                nb.create_device(
                    name=d.hostname,
                    mgmt_ip=d.management_ip,
                    vendor=d.vendor or "cisco_ios",
                    model=d.model,
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
        
        # Sync interfaces and links for this device as well
        try:
            nb = NetBoxClient.get_client()
            if nb and nb.api:
                # Interfaces
                conn_inner = _raw_conn()
                ifaces = conn_inner.execute("SELECT * FROM interfaces WHERE device_id = ?", (d.id,)).fetchall()
                for iface in ifaces:
                    if iface['ip_address']:
                        nb.assign_ip_to_interface(d.hostname, iface['name'], iface['ip_address'])
                    else:
                        nb.get_or_create_interface(d.hostname, iface['name'])
                
                # Links (only manual ones for now during registration)
                links = conn_inner.execute("""
                    SELECT l.*, sd.hostname as src_h, dd.hostname as dst_h,
                           si.name as src_i, di.name as dst_i
                    FROM links l
                    JOIN devices sd ON l.src_device_id = sd.id
                    JOIN devices dd ON l.dst_device_id = dd.id
                    JOIN interfaces si ON l.src_interface_id = si.id
                    JOIN interfaces di ON l.dst_interface_id = di.id
                    WHERE (src_device_id = ? OR dst_device_id = ?) AND l.discovery_protocol = 'manual'
                """, (d.id, d.id)).fetchall()
                for link in links:
                    nb.create_cable(link['src_h'], link['src_i'], link['dst_h'], link['dst_i'])
                conn_inner.close()
        except Exception as e:
            logger.warning("Bulk sync of components for '%s' failed: %s", d.hostname, e)

        pushed += 1

    if pushed:
        _invalidate_inventory_context(
            source="netbox_api",
            summary=f"bulk device push completed: {pushed} devices",
            metadata={"operation": "push_all", "pushed_count": pushed},
        )

    return {"pushed_count": pushed, "message": f"Pushed {pushed} devices/components to NetBox"}


# ---------------------------------------------------------------------------
#  NetBox sync endpoints
# ---------------------------------------------------------------------------

@router.post("/sync/devices")
async def sync_devices():
    """
    Sync all devices from NetBox into the local inventory.
    Runs in a thread pool so it doesn't block the event loop,
    but waits for the result before responding.
    """
    def _run():
        db = DatabaseManager(DB_PATH)
        syncer = DeviceSyncer(db)
        return syncer.sync_all_devices()

    result = await run_in_threadpool(_run)
    return result or {"status": "success", "synced_count": 0}

@router.post("/sync/topology")
async def sync_topology():
    """
    Sync topology (cables) from NetBox into the local inventory.
    Runs in a thread pool so it doesn't block the event loop,
    but waits for the result before responding.
    """
    def _run():
        db = DatabaseManager(DB_PATH)
        syncer = TopologySyncer(db)
        return syncer.sync_connections()

    result = await run_in_threadpool(_run)
    return result or {"status": "success"}


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
#  Phase 4b — Planned Topology from Local DB (works without NetBox)
# ---------------------------------------------------------------------------

@router.get("/topology/planned/local")
async def get_local_planned_topology():
    """
    Build a planned (design-of-record) topology graph from the LOCAL database.

    This uses the devices, interfaces, and links tables stored in SQLite
    to construct a graph structure compatible with the frontend's topology
    renderer (same shape as the BFS crawler output).

    Unlike the NetBox-sourced endpoint, this works even when NetBox is offline.
    """
    conn = _raw_conn()
    try:
        # 1. Fetch all devices
        devices = conn.execute(
            "SELECT id, hostname, management_ip FROM devices ORDER BY hostname"
        ).fetchall()

        nodes = {}
        device_id_to_hostname = {}

        for dev in devices:
            device_id_to_hostname[dev["id"]] = dev["hostname"]

            # 2. Fetch interfaces for this device
            ifaces = conn.execute(
                "SELECT name, ip_address FROM interfaces WHERE device_id = ? ORDER BY name",
                (dev["id"],)
            ).fetchall()

            iface_dict = {}
            for iface in ifaces:
                iface_dict[iface["name"]] = iface["ip_address"] or ""

            nodes[dev["hostname"]] = {
                "mgmt_ip": dev["management_ip"],
                "interfaceList": [iface_dict] if iface_dict else [{}],
                "connectedNode": [],
                "edgeLink": {},
            }

        # 3. Fetch all links with resolved hostnames and interface names
        links = conn.execute("""
            SELECT
                l.src_device_id, l.dst_device_id,
                l.src_interface_id, l.dst_interface_id,
                sd.hostname AS src_hostname,
                dd.hostname AS dst_hostname,
                si.name     AS src_iface_name,
                si.ip_address AS src_iface_ip,
                di.name     AS dst_iface_name,
                di.ip_address AS dst_iface_ip
            FROM links l
            JOIN devices sd ON l.src_device_id = sd.id
            JOIN devices dd ON l.dst_device_id = dd.id
            LEFT JOIN interfaces si ON l.src_interface_id = si.id
            LEFT JOIN interfaces di ON l.dst_interface_id = di.id
            ORDER BY sd.hostname
        """).fetchall()

        # 4. Build edges from links
        for link in links:
            src_h = link["src_hostname"]
            dst_h = link["dst_hostname"]
            src_iface = link["src_iface_name"] or "unknown"
            dst_iface = link["dst_iface_name"] or "unknown"
            dst_ip = link["dst_iface_ip"] or (nodes.get(dst_h, {}).get("mgmt_ip") or "")

            if src_h in nodes:
                # Add connected node (bidirectional)
                if dst_h not in nodes[src_h]["connectedNode"]:
                    nodes[src_h]["connectedNode"].append(dst_h)
                # Add edge link (src → dst)
                nodes[src_h]["edgeLink"][src_iface] = {
                    "remote_node": dst_h,
                    "remote_interface": dst_iface,
                    "remote_ip": dst_ip,
                }

            if dst_h in nodes:
                # Add reverse connected node
                if src_h not in nodes[dst_h]["connectedNode"]:
                    nodes[dst_h]["connectedNode"].append(src_h)
                # Add reverse edge link (dst → src)
                src_ip = link["src_iface_ip"] or (nodes.get(src_h, {}).get("mgmt_ip") or "")
                nodes[dst_h]["edgeLink"][dst_iface] = {
                    "remote_node": src_h,
                    "remote_interface": src_iface,
                    "remote_ip": src_ip,
                }

        return {
            "nodes": nodes,
            "source": "local_db",
            "device_count": len(nodes),
            "seeds": [],
            "timestamp": None,
        }
    except Exception as e:
        logger.error("Local planned topology build failed: %s", e)
        raise HTTPException(500, f"Failed to build local planned topology: {e}")
    finally:
        conn.close()


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
                    vendor=device_data.vendor or "cisco_ios",
                    model=device_data.model,
                )
    except Exception as e:
        logger.warning("Best-effort NetBox push failed (%s): %s", action, e)
