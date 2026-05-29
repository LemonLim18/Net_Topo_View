"""
Network — Config Backup & Restore.

Lets users store the current (live) running-config snapshot of any device
for backup and restore. Snapshots are fetched from the live device via
the driver layer and stored in config_snapshots.
"""

import logging
import difflib
from datetime import datetime
from typing import List, Optional

def get_config_diff(old_text: str, new_text: str) -> str:
    """Return a unified diff between two configurations as a string, skipping headers."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="previous_config",
            tofile="current_config",
            lineterm="",
            n=15,
        )
    )
    if len(diff) > 2:
        return "\n".join(diff[2:])
    return "\n".join(diff)


def parse_diff_to_changes(diff_text: str) -> dict:
    """
    Parse a unified diff text and group the additions/deletions by their parent Cisco config block.
    Returns a dict like: { "interface Ethernet0/1": [("-", "shutdown")] }
    """
    blocks = {}
    current_block = "Global Configuration"
    
    for line in diff_text.splitlines():
        if not line or len(line) < 2:
            continue
        prefix = line[0]
        content = line[1:]
        
        # Check if the line is diff metadata
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
            
        cleaned_content = content.strip()
        if not cleaned_content or cleaned_content == "!":
            continue
            
        if cleaned_content.startswith("Current configuration") or cleaned_content.startswith("Building configuration"):
            continue
            
        # Cisco block headers have no leading whitespace.
        # Since 'content' is prefixed with a space in diff for unchanged context lines,
        # content[0] will be a space for nested lines, and non-space for headers.
        is_header = len(content) > 0 and not content[0].isspace()
        
        if is_header:
            current_block = cleaned_content
            
        if prefix in ("+", "-"):
            if current_block not in blocks:
                blocks[current_block] = []
            blocks[current_block].append((prefix, cleaned_content))
            
    return blocks


def format_changes_summary(blocks: dict) -> str:
    """Format the parsed blocks dict into a clean string for LLM input."""
    if not blocks:
        return "No configuration changes detected."
        
    lines = []
    for block, changes in blocks.items():
        lines.append(f"Section: {block}")
        for prefix, change in changes:
            action = "Added" if prefix == "+" else "Removed"
            lines.append(f"  - {action}: {change}")
        lines.append("")  # Blank line separator
    return "\n".join(lines).strip()

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException, Response, Depends
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from db.manager import DatabaseManager
from db.models import Device
from deploy.snapshots import SnapshotManager
from auth.rbac import require_any, require_engineer

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
    description: Optional[str] = None


class SnapshotItem(BaseModel):
    id: int
    device_id: int
    captured_at: Optional[str] = None
    description: Optional[str] = None
    config_preview: str  # first ~200 chars
    config_size: int     # length of config_text
    owner_username: Optional[str] = None


class CaptureResponse(BaseModel):
    snapshot_id: int
    device_id: int
    hostname: str
    captured_at: str
    message: str


class RestoreResponse(BaseModel):
    snapshot_id: int
    device_id: int
    hostname: str
    vendor: str
    backup_snapshot_id: Optional[int] = None
    restored_snapshot_id: Optional[int] = None
    commands_applied: int
    message: str


class ExplainResponse(BaseModel):
    snapshot_id: int
    model: str = ""
    summary: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/config-backup/devices", response_model=List[DeviceSummary])
async def list_devices_for_backup(user: dict = Depends(require_any)):
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
async def list_snapshots(
    device_id: int,
    limit: int = 20,
    user: dict = Depends(require_any),
):
    """List stored config snapshots for a device (newest first)."""
    def _run():
        db = _db()
        if db.get_device_by_id(device_id) is None:
            raise HTTPException(404, f"Device id {device_id} not found")
        is_admin = user.get("role") == "admin"
        owner_username = user.get("username")
        history = db.get_config_history(
            device_id,
            limit=limit,
            owner_username=owner_username,
            is_admin=is_admin,
        )
        if owner_username and owner_username != "anonymous":
            for snap in history:
                if getattr(snap, "id", None) and not getattr(snap, "owner_username", None):
                    try:
                        db.update_snapshot_owner(snap.id, owner_username)
                        snap.owner_username = owner_username
                    except Exception:
                        logger.debug("Could not backfill owner for snapshot %s", getattr(snap, "id", None))
        return [
            SnapshotItem(
                id=s.id,
                device_id=s.device_id,
                captured_at=s.captured_at,
                description=getattr(s, "description", None),
                config_preview=(s.config_text or "")[:200].replace("\r", ""),
                config_size=len(s.config_text or ""),
                owner_username=getattr(s, "owner_username", None),
            )
            for s in history
            if s.id is not None
        ]

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise


@router.get("/config-backup/snapshots/{snapshot_id}/download")
async def download_snapshot(snapshot_id: int, user: dict = Depends(require_any)):
    """Return the full config text for a snapshot (for download as file)."""


    def _run():
        db = _db()
        snap = db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"Snapshot {snapshot_id} not found")
        if user.get("role") != "admin" and snap.owner_username != user.get("username"):
            raise HTTPException(403, "Access to this snapshot is restricted to its owner and administrators.")
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
async def get_snapshot_content(snapshot_id: int, user: dict = Depends(require_any)):
    """Return the full config text (JSON) for a snapshot for in-browser viewing."""
    def _run():
        db = _db()
        snap = db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"Snapshot {snapshot_id} not found")
        if user.get("role") != "admin" and snap.owner_username != user.get("username"):
            raise HTTPException(403, "Access to this snapshot is restricted to its owner and administrators.")
        return {"id": snap.id, "config_text": snap.config_text or ""}

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise


@router.get("/config-backup/snapshots/{snapshot_id}/diff")
async def get_snapshot_diff(snapshot_id: int, user: dict = Depends(require_any)):
    """
    Generate a unified diff between the current snapshot and the previous snapshot (if any),
    returning the diff text, plus count of added and deleted lines.
    """
    def _run():
        db = _db()
        snap = db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"Snapshot {snapshot_id} not found")
        if user.get("role") != "admin" and snap.owner_username != user.get("username"):
            raise HTTPException(403, "Access to this snapshot is restricted to its owner and administrators.")

        # Find the previous snapshot for this device
        prev_snap = None
        try:
            history = db.get_config_history(snap.device_id, limit=50)
            sorted_history = sorted(history, key=lambda s: s.id, reverse=True)
            for i, s in enumerate(sorted_history):
                if s.id == snap.id:
                    if i + 1 < len(sorted_history):
                        prev_snap = sorted_history[i + 1]
                    break
        except Exception as e:
            logger.warning(f"Failed to find previous snapshot: {e}")

        old_text = prev_snap.config_text or "" if prev_snap else ""
        new_text = snap.config_text or ""

        # Compute diff using difflib
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        
        diff_lines = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"snapshot_{prev_snap.id}" if prev_snap else "none",
            tofile=f"snapshot_{snap.id}",
            lineterm="",
            n=15,
        ))

        # Skip headers
        if len(diff_lines) > 2:
            diff_lines = diff_lines[2:]

        # Count additions and deletions
        additions = 0
        deletions = 0
        for line in diff_lines:
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        return {
            "snapshot_id": snapshot_id,
            "has_previous": prev_snap is not None,
            "previous_snapshot_id": prev_snap.id if prev_snap else None,
            "additions": additions,
            "deletions": deletions,
            "diff_text": "\n".join(diff_lines)
        }

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise


@router.post("/config-backup/snapshots/{snapshot_id}/explain", response_model=ExplainResponse)
async def explain_snapshot(snapshot_id: int, user: dict = Depends(require_any)):
    """
    Use the configured LLM provider to summarize a stored backup snapshot.
    Reads the snapshot text from the local DB (not from the live device).
    """

    def _run() -> ExplainResponse:
        db = _db()
        snap = db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"Snapshot {snapshot_id} not found")
        if user.get("role") != "admin" and snap.owner_username != user.get("username"):
            raise HTTPException(403, "Access to this snapshot is restricted to its owner and administrators.")

        from llm.provider import get_llm_provider
        llm = get_llm_provider()

        cfg = (snap.config_text or "").strip()
        if not cfg:
            return ExplainResponse(
                snapshot_id=snapshot_id,
                model=llm.model_name(),
                summary="This snapshot has no config text stored.",
            )

        # Keep prompts bounded so very large configs don't stall the UI.
        max_chars = 12000
        cfg_prompt = cfg if len(cfg) <= max_chars else (cfg[:max_chars] + "\n\n... <truncated> ...\n")

        prompt = (
            "You are a network automation reviewer.\n"
            "Summarize the following network device configuration snapshot for a human operator.\n"
            "Output format:\n"
            "1) High-level purpose (1-2 sentences)\n"
            "2) Key features detected (bullets)\n"
            "3) Potential risks / things to double-check before changes (bullets)\n"
            "4) Notable identifiers (hostname, mgmt IPs, routing protocols) if present (bullets)\n\n"
            "CONFIG SNAPSHOT:\n"
            "```\n"
            f"{cfg_prompt}\n"
            "```"
        )

        try:
            summary = (llm.generate_sync(prompt, temperature=0.2) or "").strip()
        except Exception as exc:
            # Don't fail the whole UI because the LLM is down.
            summary = f"AI summary unavailable: {str(exc).strip() or 'unknown error'}"

        return ExplainResponse(snapshot_id=snapshot_id, model=llm.model_name(), summary=summary)

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise


@router.post("/config-backup/capture", response_model=CaptureResponse)
async def capture_snapshot(body: CaptureRequest, user: dict = Depends(require_engineer)):
    """
    Capture the current running configuration from the **live device**
    and store it as a backup snapshot. Requires the device to be reachable
    (SSH) and credentials to be configured.
    """
    db = _db()
    device = db.get_device_by_id(body.device_id)
    if device is None:
        raise HTTPException(404, f"Device id {body.device_id} not found")

    # Prevent concurrent redundant duplicate captures within a 5-second window
    try:
        history = db.get_config_history(body.device_id, limit=1)
        if history:
            latest = history[0]
            if latest.captured_at:
                captured_dt = datetime.fromisoformat(latest.captured_at)
                diff = (datetime.utcnow() - captured_dt).total_seconds()
                if diff < 5.0:
                    if not getattr(latest, "owner_username", None) and user.get("username"):
                        try:
                            db.update_snapshot_owner(latest.id, user.get("username"))
                            latest.owner_username = user.get("username")
                        except Exception:
                            logger.debug("Could not backfill owner on recent snapshot %s", latest.id)
                    logger.info(
                        "Returning existing snapshot #%d for device %d (captured %.1fs ago) to prevent redundant duplicate",
                        latest.id, body.device_id, diff
                    )
                    return CaptureResponse(
                        snapshot_id=latest.id,
                        device_id=body.device_id,
                        hostname=device.hostname,
                        captured_at=latest.captured_at,
                        message=f"Returned existing snapshot for {device.hostname} (captured recently).",
                    )
    except Exception as e:
        logger.warning(f"Error checking recent snapshots: {e}")

    def _run() -> Optional[int]:
        sm = _snapshot_manager()
        # label "backup" for user-initiated backup (vs deploy pre/post)
        return sm.capture(
            body.device_id,
            label="backup",
            description=body.description,
            owner_username=user.get("username"),
        )

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
    if snap is not None and not getattr(snap, "owner_username", None) and user.get("username"):
        try:
            db.update_snapshot_owner(snapshot_id, user.get("username"))
            snap = db.get_config_snapshot(snapshot_id)
        except Exception:
            logger.debug("Could not persist owner for snapshot %s after capture", snapshot_id)
    captured_at = snap.captured_at if snap else ""

    return CaptureResponse(
        snapshot_id=snapshot_id,
        device_id=body.device_id,
        hostname=device.hostname,
        captured_at=captured_at,
        message=f"Stored running-config snapshot for {device.hostname}. Use for backup/restore.",
    )


@router.post("/config-backup/snapshots/{snapshot_id}/restore", response_model=RestoreResponse)
async def restore_snapshot(snapshot_id: int, user: dict = Depends(require_engineer)):
    """
    Apply a stored snapshot back to the live device.

    Safety behavior:
    - checks snapshot ownership / admin access
    - captures a fresh safety backup before restore
    - currently enabled only for Cisco IOS/IOS-XE and Huawei VRP
    """

    def _run() -> RestoreResponse:
        db = _db()
        snap = db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"Snapshot {snapshot_id} not found")

        username = user.get("username")
        if not getattr(snap, "owner_username", None) and username and username != "anonymous":
            try:
                db.update_snapshot_owner(snapshot_id, username)
                snap.owner_username = username
            except Exception:
                logger.debug("Could not backfill owner before restore for snapshot %s", snapshot_id)

        if user.get("role") != "admin" and snap.owner_username != username:
            raise HTTPException(403, "Access to this snapshot is restricted to its owner and administrators.")

        sm = _snapshot_manager()
        try:
            result = sm.restore_snapshot(snapshot_id, owner_username=username)
        except ValueError as exc:
            raise HTTPException(422, str(exc).strip() or "Snapshot restore could not be performed")
        except HTTPException:
            raise
        except Exception as exc:
            msg = str(exc).strip() or "Unknown restore failure"
            raise HTTPException(502, f"Failed to restore snapshot #{snapshot_id}: {msg}")

        return RestoreResponse(**result)

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise


# ---------------------------------------------------------------------------
#  POST-CAPTURE: update description + AI description suggestion
# ---------------------------------------------------------------------------


class DescriptionUpdate(BaseModel):
    description: Optional[str] = None


class DescriptionSuggestion(BaseModel):
    description: str = ""


@router.patch("/config-backup/snapshots/{snapshot_id}/description")
async def update_snapshot_description(
    snapshot_id: int,
    body: DescriptionUpdate,
    user: dict = Depends(require_engineer),
):
    """Update the description of an existing snapshot (e.g. after capture)."""

    def _run():
        db = _db()
        snap = db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"Snapshot {snapshot_id} not found")
        if user.get("role") != "admin" and snap.owner_username != user.get("username"):
            raise HTTPException(403, "Access to this snapshot is restricted to its owner and administrators.")
        db.update_snapshot_description(snapshot_id, (body.description or "").strip() or None)
        return {"snapshot_id": snapshot_id, "description": body.description}

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise


@router.post(
    "/config-backup/snapshots/{snapshot_id}/suggest-description",
    response_model=DescriptionSuggestion,
)
async def suggest_snapshot_description(snapshot_id: int, user: dict = Depends(require_any)):
    """
    Use the configured LLM to generate a description for a stored config backup snapshot,
    comparing it to the previous snapshot if available to describe the changes.
    """

    def _run() -> DescriptionSuggestion:
        db = _db()
        snap = db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"Snapshot {snapshot_id} not found")
        if user.get("role") != "admin" and snap.owner_username != user.get("username"):
            raise HTTPException(403, "Access to this snapshot is restricted to its owner and administrators.")

        from llm.provider import get_llm_provider

        llm = get_llm_provider()

        cfg = (snap.config_text or "").strip()
        if not cfg:
            return DescriptionSuggestion(description="Empty configuration snapshot")

        # Find the previous snapshot for this device
        prev_snap = None
        try:
            history = db.get_config_history(snap.device_id, limit=50)
            sorted_history = sorted(history, key=lambda s: s.id, reverse=True)
            for i, s in enumerate(sorted_history):
                if s.id == snap.id:
                    if i + 1 < len(sorted_history):
                        prev_snap = sorted_history[i + 1]
                    break
        except Exception as e:
            logger.warning(f"Failed to find previous snapshot for diffing: {e}")

        if prev_snap and prev_snap.config_text:
            diff_text = get_config_diff(prev_snap.config_text, snap.config_text)
            parsed_blocks = parse_diff_to_changes(diff_text)
            changes_summary = format_changes_summary(parsed_blocks)
            
            if not parsed_blocks:
                prompt = (
                    "You are a network automation assistant.\n"
                    "We captured a new configuration snapshot of a network device, but NO changes were detected "
                    "compared to the previous configuration snapshot.\n"
                    "Write a concise summary describing the current configuration baseline. Keep the description under 500 characters. "
                    "Do NOT include markdown formatting, quotes, or newlines. Just the plain text description."
                )
            else:
                prompt = (
                    "You are a network automation assistant.\n"
                    "We captured a new configuration snapshot of a network device. Here is a structured summary of the changes "
                    "compared to the previous snapshot (lines prefixed with 'Added' were added, 'Removed' were deleted under each section):\n\n"
                    "CHANGES:\n"
                    f"{changes_summary}\n\n"
                    "Write a clear, direct, and complete summary of the changes (e.g. 'Interface Ethernet0/1 was enabled by removing shutdown.'). "
                    "Be highly specific and refer to the exact interface or section names. Keep the summary under 400 characters.\n"
                    "CRITICAL: Do NOT use markdown formatting (like asterisks, bolding, bullets, or backticks), quotes, or newlines. The output MUST be a single, cohesive paragraph of plain text without lists."
                )
        else:
            max_chars = 8000
            cfg_prompt = cfg if len(cfg) <= max_chars else (cfg[:max_chars] + "\n... <truncated> ...")
            prompt = (
                "You are a network automation assistant.\n"
                "Given the following network device running-configuration, write a summary description of what this config is about "
                "(e.g., device hostname, role, management IP, key protocols, interfaces).\n"
                "This is the baseline snapshot (no previous configuration to compare against).\n"
                "Keep the description under 500 characters. Do NOT use markdown formatting, quotes, or newlines. Just plain text.\n\n"
                "CONFIG:\n```\n"
                f"{cfg_prompt}\n"
                "```"
            )

        try:
            desc = (llm.generate_sync(prompt, temperature=0.2) or "").strip()
            # Sanitize: remove surrounding quotes if the model wraps them
            if desc.startswith('"') and desc.endswith('"'):
                desc = desc[1:-1].strip()
            if desc.startswith("'") and desc.endswith("'"):
                desc = desc[1:-1].strip()
            # Enforce max length matching UI's limit (500)
            if len(desc) > 500:
                desc = desc[:497] + "..."
        except Exception as exc:
            desc = f"AI suggestion unavailable: {str(exc).strip() or 'unknown error'}"

        return DescriptionSuggestion(description=desc)

    try:
        return await run_in_threadpool(_run)
    except HTTPException:
        raise
