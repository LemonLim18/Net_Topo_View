"""
Network — Config Backup & Restore.

Lets users store the current (live) running-config snapshot of any device
for backup and restore. Snapshots are fetched from the live device via
the driver layer and stored in config_snapshots.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from db.manager import DatabaseManager
from db.models import Device
from deploy.snapshots import SnapshotManager

logger = logging.getLogger("network.api")

router = APIRouter(prefix="/network", tags=["Network"])

DB_PATH = "db/netauto.db"
_singleton_db: Optional[DatabaseManager] = None


def set_db_manager(db: DatabaseManager):
    global _singleton_db
    _singleton_db = db


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _db() -> DatabaseManager:
    return _singleton_db or DatabaseManager(DB_PATH)


def _snapshot_manager() -> SnapshotManager:
    return SnapshotManager(_db())


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DeviceSummary(BaseModel):
    id: int
    hostname: str
    management_ip: Optional[str] = None
    vendor: Optional[str] = None
    status: Optional[str] = None
    role: Optional[str] = None
    # Read-only: True if device appears in current live topology cache (not written to DB).
    live_in_topology: Optional[bool] = None


class CaptureRequest(BaseModel):
    device_id: int


class SnapshotItem(BaseModel):
    id: int
    device_id: int
    captured_at: Optional[str] = None
    config_preview: str  # first ~200 chars
    config_size: int     # length of config_text


class CaptureResponse(BaseModel):
    snapshot_id: int
    device_id: int
    hostname: str
    captured_at: str
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/config-backup/devices", response_model=List[DeviceSummary])
async def list_devices_for_backup():
    """
    List devices that can be used for config backup.
    Uses the same inventory as Device Inventory (manual + NetBox). Each device
    includes live_in_topology: whether it appears in the current live topology
    cache (read from crawler cache only; no DB or inventory updates).
    """
    def _run():
        db = _db()
        devices = db.get_all_devices()
        # Read live topology cache only (no writes)
        try:
            topo = db.get_topology()
            nodes = topo.get("nodes") or {}
        except Exception:
            nodes = {}
        # Live nodes matching sets
        live_hostnames_lower = set()
        live_shortnames_lower = set()
        live_mgmt_ips_clean = set()

        for h, nd in nodes.items():
            if not isinstance(nd, dict) or nd.get("down"):
                continue
            h_lower = h.lower()
            live_hostnames_lower.add(h_lower)
            if "." in h_lower:
                live_shortnames_lower.add(h_lower.split(".")[0])
            
            mgmt = nd.get("mgmt_ip")
            if mgmt and isinstance(mgmt, str):
                ip_clean = mgmt.split("/")[0].strip()
                live_mgmt_ips_clean.add(ip_clean)

        # Match device to topology by hostname (full or short) or by management IP
        def in_live_topo(device: Device) -> bool:
            if device.hostname:
                h_lower = device.hostname.lower()
                if h_lower in live_hostnames_lower:
                    return True
                if "." in h_lower and h_lower.split(".")[0] in live_shortnames_lower:
                    return True
                if h_lower in live_shortnames_lower:
                    return True

            if device.management_ip:
                ip_clean = device.management_ip.split("/")[0].strip()
                if ip_clean in live_mgmt_ips_clean:
                    return True
            return False

        return [
            DeviceSummary(
                id=d.id,
                hostname=d.hostname,
                management_ip=d.management_ip,
                vendor=d.vendor,
                status=d.status,
                role=d.role,
                live_in_topology=in_live_topo(d),
            )
            for d in devices
            if d.id is not None
        ]

    return await run_in_threadpool(_run)


@router.get("/config-backup/snapshots", response_model=List[SnapshotItem])
async def list_snapshots(device_id: int, limit: int = 20):
    """List stored config snapshots for a device (newest first)."""
    def _run():
        db = _db()
        if db.get_device_by_id(device_id) is None:
            raise HTTPException(404, f"Device id {device_id} not found")
        history = db.get_config_history(device_id, limit=limit)
        return [
            SnapshotItem(
                id=s.id,
                device_id=s.device_id,
                captured_at=s.captured_at,
                config_preview=(s.config_text or "")[:200].replace("\r", ""),
                config_size=len(s.config_text or ""),
            )
            for s in history
            if s.id is not None
        ]

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise


@router.get("/config-backup/snapshots/{snapshot_id}/download")
async def download_snapshot(snapshot_id: int):
    """Return the full config text for a snapshot (for download as file)."""
    from fastapi.responses import Response

    def _run():
        db = _db()
        snap = db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"Snapshot {snapshot_id} not found")
        return snap.config_text or ""

    try:
        config_text = await run_in_threadpool(_run)
    except HTTPException:
        raise
    return Response(
        content=config_text,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="config_snapshot_{snapshot_id}.txt"',
        },
    )


@router.get("/config-backup/snapshots/{snapshot_id}/content")
async def get_snapshot_content(snapshot_id: int):
    """Return the full config text (JSON) for a snapshot for in-browser viewing."""
    def _run():
        db = _db()
        snap = db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"Snapshot {snapshot_id} not found")
        return {"id": snap.id, "config_text": snap.config_text or ""}

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise


@router.post("/config-backup/capture", response_model=CaptureResponse)
async def capture_snapshot(body: CaptureRequest):
    """
    Capture the current running configuration from the **live device**
    and store it as a backup snapshot. Requires the device to be reachable
    (SSH) and credentials to be configured.
    """
    db = _db()
    device = db.get_device_by_id(body.device_id)
    if device is None:
        raise HTTPException(404, f"Device id {body.device_id} not found")

    def _run() -> Optional[int]:
        sm = _snapshot_manager()
        # label "backup" for user-initiated backup (vs deploy pre/post)
        return sm.capture(body.device_id, label="backup")

    try:
        snapshot_id = await run_in_threadpool(_run)
    except Exception as exc:
        msg = str(exc).strip() or "Unknown error"
        raise HTTPException(
            502,
            f"Failed to capture config from {device.hostname}: {msg}",
        )
    if snapshot_id is None:
        raise HTTPException(
            502,
            f"Failed to capture config from {device.hostname}. "
            "Check device reachability and SSH credentials (Device Inventory / Onboard).",
        )

    # Fetch the snapshot to get captured_at
    snap = db.get_config_snapshot(snapshot_id)
    captured_at = snap.captured_at if snap else ""

    return CaptureResponse(
        snapshot_id=snapshot_id,
        device_id=body.device_id,
        hostname=device.hostname,
        captured_at=captured_at,
        message=f"Stored running-config snapshot for {device.hostname}. Use for backup/restore.",
    )
