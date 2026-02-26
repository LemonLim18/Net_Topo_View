"""
M0 — Onboarding API
REST + WebSocket endpoints for the Device Onboarding Wizard.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .config_generator import OnboardingConfigGenerator
from .connectivity_poller import get_poller, STATUS_REGISTERED, STATUS_FAILED

logger = logging.getLogger("onboarding.api")

router = APIRouter(prefix="/onboarding", tags=["Onboarding (M0)"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class OnboardingParams(BaseModel):
    hostname:        str
    mgmt_ip:         str
    subnet_mask:     str  = "255.255.255.0"
    gateway:         str
    mgmt_interface:  str  = "GigabitEthernet0/0"
    vendor:          str  = "cisco_ios"   # cisco_ios | cisco_xe | huawei_vrp | juniper_junos
    ssh_user:        str
    ssh_password:    str
    port:            int  = Field(22, ge=1, le=65535)
    timeout_minutes: int  = Field(10, ge=1, le=60)


class GenerateResponse(BaseModel):
    config: str


class PollStartResponse(BaseModel):
    device_id: int
    status:    str


class PollStatusResponse(BaseModel):
    device_id:    int
    status:       str
    attempts:     int
    last_attempt: Optional[str]


# ---------------------------------------------------------------------------
# POST /generate  — produce Day-0 CLI block
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=GenerateResponse)
async def generate_config(params: OnboardingParams):
    """Return a vendor-specific Day-0 config block for console-paste."""
    config = OnboardingConfigGenerator().generate(params.model_dump())
    return GenerateResponse(config=config)


# ---------------------------------------------------------------------------
# POST /start-poll  — register device placeholder + start SSH polling
# ---------------------------------------------------------------------------

@router.post("/start-poll", response_model=PollStartResponse)
async def start_poll(params: OnboardingParams):
    """
    Create a placeholder device record and begin SSH polling in the background.
    Returns immediately with the assigned device_id.
    """
    poller    = get_poller()
    device_id = poller.start_session(params.model_dump())

    asyncio.create_task(
        poller.poll_loop(
            device_id=device_id,
            timeout_minutes=params.timeout_minutes,
        )
    )

    return PollStartResponse(device_id=device_id, status="polling")


# ---------------------------------------------------------------------------
# GET /status/{device_id}  — current poll state
# ---------------------------------------------------------------------------

@router.get("/status/{device_id}", response_model=PollStatusResponse)
async def poll_status(device_id: int):
    """Return the current polling state for a device."""
    poller = get_poller()
    state  = poller.get_status(device_id)
    return PollStatusResponse(
        device_id    = state["device_id"],
        status       = state["status"],
        attempts     = state.get("attempts", 0),
        last_attempt = state.get("last_attempt"),
    )


# ---------------------------------------------------------------------------
# WebSocket /ws/{device_id}  — stream poll events in real time
# ---------------------------------------------------------------------------

@router.websocket("/ws/{device_id}")
async def poll_websocket(websocket: WebSocket, device_id: int, timeout_minutes: int = 10):
    """
    Open this WebSocket BEFORE or immediately AFTER calling /start-poll.
    Events streamed: attempt | connected | registered | timeout
    """
    await websocket.accept()
    poller = get_poller()

    # Queue used as the bridge between the poll loop and this WS handler
    queue: asyncio.Queue = asyncio.Queue()

    async def broadcast(event_dict: dict) -> None:
        await queue.put(event_dict)

    # Check whether a session already exists; if not, create one on the fly
    state = poller.get_status(device_id)
    if state["status"] == "unknown":
        # Client opened WS without calling /start-poll first — tell them
        await websocket.send_json({"event": "error", "message": "No active onboarding session for this device_id. Call /start-poll first."})
        await websocket.close()
        return

    # Register our broadcast function and restart the poll loop if still pending
    session = poller._sessions.get(device_id)
    if session is not None:
        session["broadcast"] = broadcast
        # If the background task hasn't started yet or was created without a
        # broadcast target, launch a new one that will use our queue.
        current_status = session["status"]
        if current_status in ("pending", "polling"):
            asyncio.create_task(
                poller.poll_loop(
                    device_id=device_id,
                    timeout_minutes=timeout_minutes,
                    broadcast=broadcast,
                )
            )

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                await websocket.send_json(event)

                # Terminal events — close gracefully
                if event.get("event") in ("registered", "timeout"):
                    break
            except asyncio.TimeoutError:
                # Heartbeat — keep connection alive and check if terminal
                final = poller.get_status(device_id)
                if final["status"] in (STATUS_REGISTERED, STATUS_FAILED):
                    break
                # Send a keepalive ping
                try:
                    await websocket.send_json({"event": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        logger.info("WS client disconnected from onboarding stream for device_id=%d", device_id)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
