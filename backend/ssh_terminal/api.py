"""
SSH Terminal API
=================
REST + WebSocket endpoints for interactive SSH terminal sessions.

Security:
    - All REST endpoints require a valid JWT (via get_current_user dependency).
    - WebSocket auth: ?token=<jwt> query param; ownership verified before accept.
    - Passwords are NEVER logged at any level.

Admission control:
    - MAX_SESSIONS_PER_USER / MAX_SESSIONS_PER_HOST enforced on creation.

Vault integration:
    - POST /ssh/sessions/connect accepts save_to_vault=true to persist creds.
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from auth.auth import get_current_user, decode_token
from db.manager import DatabaseManager
from auth.credentials import CredentialVault
from ssh_terminal.session import (
    MAX_SESSIONS_PER_HOST,
    MAX_SESSIONS_PER_USER,
    count_sessions_for_host,
    count_sessions_for_user,
    create_session,
    get_session,
    remove_session,
    list_sessions,
)

logger = logging.getLogger("ssh_terminal.api")
router = APIRouter(tags=["SSH Terminal"])

_db = DatabaseManager()
_vault = CredentialVault()


# ---------- Models ----------

class CreateSessionRequest(BaseModel):
    hostname: str
    port: int = 22


class ConnectSessionRequest(BaseModel):
    hostname: str
    port: int = 22
    username: str
    password: str
    save_to_vault: bool = False


class ConnectByDeviceRequest(BaseModel):
    """Connect using credentials stored on the device in the local DB."""
    hostname: str
    port: int = 22


# ---------- Helpers ----------

def _resolve_target(hostname: str):
    """Return (device, target_host). device may be None if not in DB."""
    device = _db.get_device_by_hostname(hostname)
    target_host = (device.management_ip if device and device.management_ip else hostname)
    return device, target_host


def _check_limits(owner: str, host: str) -> None:
    """Raise HTTP 429 if per-user or per-host session limits are exceeded."""
    if count_sessions_for_user(owner) >= MAX_SESSIONS_PER_USER:
        raise HTTPException(
            status_code=429,
            detail=f"Per-user session limit ({MAX_SESSIONS_PER_USER}) reached. Close a tab first.",
        )
    if count_sessions_for_host(host) >= MAX_SESSIONS_PER_HOST:
        raise HTTPException(
            status_code=429,
            detail=f"Per-host session limit ({MAX_SESSIONS_PER_HOST}) reached for {host}.",
        )


# ---------- REST endpoints ----------

@router.post("/ssh/sessions")
async def create_ssh_session(
    req: CreateSessionRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Create a session and attempt auto-connect using vault credentials.
    Returns status "connected" or "credentials_required".
    """
    device, target_host = _resolve_target(req.hostname)
    owner = current_user["username"]

    _check_limits(owner, target_host)

    creds = None
    if device and device.id:
        creds = _vault.get(device.id, "SSH")

    session = create_session(host=target_host, port=req.port, owner_username=owner)

    if creds:
        try:
            session.connect(username=creds["username"], password=creds["password"])
            return {
                "status": "connected",
                "session_id": session.session_id,
                "host": target_host,
                "hostname": req.hostname,
            }
        except Exception as exc:
            remove_session(session.session_id)
            logger.warning("Vault-cred SSH failed for %s: %s", target_host, exc)
            raise HTTPException(status_code=502, detail=f"SSH connection failed: {exc}")
    else:
        return {
            "status": "credentials_required",
            "session_id": session.session_id,
            "host": target_host,
            "hostname": req.hostname,
        }


@router.post("/ssh/sessions/connect")
async def connect_ssh_session_with_creds(
    req: ConnectSessionRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Create + immediately connect a session with explicit credentials.
    If save_to_vault is True the credentials are stored in the Fernet vault.
    The password is NEVER included in any log message.
    """
    device, target_host = _resolve_target(req.hostname)
    owner = current_user["username"]

    _check_limits(owner, target_host)

    session = create_session(host=target_host, port=req.port, owner_username=owner)
    try:
        session.connect(username=req.username, password=req.password)
    except Exception as exc:
        remove_session(session.session_id)
        # Password intentionally omitted from log
        logger.warning("SSH connect failed for %s@%s: %s", req.username, target_host, exc)
        raise HTTPException(status_code=502, detail=f"SSH connection failed: {exc}")

    saved = False
    if req.save_to_vault and device and device.id:
        try:
            _vault.store("SSH", device.id, req.username, req.password)
            logger.info("Stored SSH credentials for device %d in vault", device.id)
            saved = True
        except Exception as vault_exc:
            logger.warning("Failed to save credentials to vault: %s", vault_exc)

    return {
        "status": "connected",
        "session_id": session.session_id,
        "host": target_host,
        "hostname": req.hostname,
        "saved_to_vault": saved,
    }


@router.post("/ssh/sessions/connect-by-device")
async def connect_ssh_session_by_device(
    req: ConnectByDeviceRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Create and connect a session using credentials stored on the device in the local DB.
    Use this for one-click SSH from the device list when the device has ssh_user/ssh_password set.
    """
    device, target_host = _resolve_target(req.hostname)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device '{req.hostname}' not found in inventory")
    if not (getattr(device, "ssh_user", None) and getattr(device, "ssh_password", None)):
        raise HTTPException(
            status_code=400,
            detail=f"Device '{req.hostname}' has no stored SSH credentials. Use Custom SSH or set credentials in Device Inventory.",
        )
    owner = current_user["username"]
    _check_limits(owner, target_host)

    port = req.port or 22
    if getattr(device, "ssh_port", None) is not None:
        port = device.ssh_port or 22

    session = create_session(host=target_host, port=port, owner_username=owner)
    try:
        session.connect(username=device.ssh_user, password=device.ssh_password)
    except Exception as exc:
        remove_session(session.session_id)
        logger.warning("Device-cred SSH failed for %s: %s", target_host, exc)
        raise HTTPException(status_code=502, detail=f"SSH connection failed: {exc}")

    return {
        "status": "connected",
        "session_id": session.session_id,
        "host": target_host,
        "hostname": req.hostname,
        "saved_to_vault": False,
    }


@router.get("/ssh/sessions")
async def list_ssh_sessions(current_user: dict = Depends(get_current_user)):
    """List sessions. Admins see all; regular users see only their own."""
    all_sessions = list_sessions()
    if current_user["role"] == "admin":
        return all_sessions
    return [s for s in all_sessions if s["owner_username"] == current_user["username"]]


@router.delete("/ssh/sessions/{session_id}")
async def close_ssh_session(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Close a session. Users may only close their own; admins can close any."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if current_user["role"] != "admin" and session.owner_username != current_user["username"]:
        raise HTTPException(status_code=403, detail="Cannot close another user's session")
    remove_session(session_id)
    return {"status": "closed", "session_id": session_id}


# ---------- WebSocket endpoint ----------

@router.websocket("/ws/ssh/{session_id}")
async def ssh_terminal_ws(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(None, description="JWT access token"),
):
    """
    Bidirectional WebSocket for an SSH terminal session.

    Auth: pass the JWT as ?token=<jwt> in the WS URL.

    Client → Server (JSON):
        {"type": "input",  "data": "<keystrokes>"}
        {"type": "resize", "cols": 120, "rows": 40}
        {"type": "ping"}

    Server → Client (JSON):
        {"type": "output",     "data": "<terminal output>"}
        {"type": "error",      "data": "<message>"}
        {"type": "disconnect"}
        {"type": "pong"}
    """
    # --- Auth: validate token BEFORE accepting ---
    if not token:
        await websocket.close(code=4001, reason="Authentication required: pass ?token=<jwt>")
        return

    try:
        payload = decode_token(token)
        token_user = payload.get("username", "anonymous")
        token_role = payload.get("role", "viewer")
    except Exception:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    # --- Session lookup ---
    session = get_session(session_id)
    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return

    # --- Ownership check ---
    if token_role != "admin" and session.owner_username != token_user:
        await websocket.close(code=4003, reason="Access denied: not your session")
        return

    if not session.connected:
        await websocket.close(code=4003, reason="Session not connected")
        return

    await websocket.accept()
    logger.info("WS opened for SSH session %s (user: %s)", session_id, token_user)

    async def read_ssh_output():
        """Background task: poll SSH channel and forward output to WebSocket."""
        try:
            while session.connected:
                data = await asyncio.get_event_loop().run_in_executor(None, session.read)
                if data:
                    await websocket.send_json({
                        "type": "output",
                        "data": data.decode("utf-8", errors="replace"),
                    })
                else:
                    await asyncio.sleep(0.05)
        except Exception as exc:
            logger.debug("SSH read loop ended for %s: %s", session_id, exc)
        finally:
            try:
                await websocket.send_json({"type": "disconnect"})
            except Exception:
                pass

    reader_task = asyncio.create_task(read_ssh_output())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                session.write(raw)
                continue

            msg_type = msg.get("type", "input")
            if msg_type == "input":
                # Input content (keystrokes) intentionally not logged
                session.write(msg.get("data", ""))
            elif msg_type == "resize":
                session.resize(msg.get("cols", 120), msg.get("rows", 40))
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        logger.info("WS disconnected for SSH session %s (user: %s)", session_id, token_user)
    except Exception as exc:
        logger.error("WS error for SSH session %s: %s", session_id, exc)
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
        logger.info("WS closed for SSH session %s", session_id)
