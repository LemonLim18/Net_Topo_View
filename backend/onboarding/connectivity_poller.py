"""
M0 — Connectivity Poller
Polls a device's SSH port every 10 s until it responds, then auto-registers
the device in the inventory. Tracks in-memory state per session.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from db.manager import DatabaseManager
from db.models import Device
from db.repositories.device_repo import DeviceRepository

logger = logging.getLogger("onboarding.poller")

# Status constants
STATUS_PENDING     = "pending"
STATUS_POLLING     = "polling"
STATUS_CONNECTED   = "connected"
STATUS_REGISTERED  = "registered"
STATUS_FAILED      = "failed"

_POLL_INTERVAL_SEC = 10
_TCP_TIMEOUT_SEC   = 5


class ConnectivityPoller:
    """
    Singleton-ish poller.  Module consumers share one instance via
    ``get_poller()`` so session state is stable across request handlers.
    """

    def __init__(self) -> None:
        self._sessions: Dict[int, dict] = {}              # device_id → session
        self._db: Optional[DatabaseManager] = None

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def _get_db(self) -> DatabaseManager:
        if self._db is None:
            self._db = DatabaseManager()
        return self._db

    # ------------------------------------------------------------------
    # Session start
    # ------------------------------------------------------------------

    def start_session(self, params: dict) -> int:
        """
        Insert a placeholder Device row and return its id.
        The device is stored with status ``pending_onboard`` so it appears
        in inventory even before SSH is confirmed.
        """
        db  = self._get_db()
        repo = DeviceRepository(db)

        device = Device(
            hostname      = params["hostname"],
            management_ip = params["mgmt_ip"],
            vendor        = params["vendor"],
            status        = "pending_onboard",
        )
        device_id = repo.save(device)

        self._sessions[device_id] = {
            "device_id":    device_id,
            "status":       STATUS_PENDING,
            "attempts":     0,
            "last_attempt": None,
            "params":       params,
            "broadcast":    None,
        }
        logger.info("Onboarding session started for device_id=%d", device_id)
        return device_id

    # ------------------------------------------------------------------
    # Status query
    # ------------------------------------------------------------------

    def get_status(self, device_id: int) -> dict:
        if device_id not in self._sessions:
            return {"device_id": device_id, "status": "unknown"}
        s = self._sessions[device_id]
        return {
            "device_id":    s["device_id"],
            "status":       s["status"],
            "attempts":     s["attempts"],
            "last_attempt": s["last_attempt"],
        }

    # ------------------------------------------------------------------
    # Async poll loop  (launched as a background task)
    # ------------------------------------------------------------------

    async def poll_loop(
        self,
        device_id: int,
        timeout_minutes: int = 10,
        broadcast: Optional[Callable] = None,
    ) -> None:
        """
        Check TCP reachability every 10 s.
        Calls ``broadcast(event_dict)`` if provided so the WebSocket
        endpoint can stream progress without being coupled here.
        """
        session = self._sessions.get(device_id)
        if session is None:
            logger.error("poll_loop: no session for device_id=%d", device_id)
            return

        session["status"]    = STATUS_POLLING
        session["broadcast"] = broadcast

        params          = session["params"]
        host            = params["mgmt_ip"]
        port            = int(params.get("port", 22))
        deadline        = asyncio.get_event_loop().time() + timeout_minutes * 60

        async def _emit(event: str, message: str) -> None:
            session["last_attempt"] = datetime.now(timezone.utc).isoformat()
            if broadcast:
                try:
                    await broadcast({"event": event, "message": message, "attempts": session["attempts"]})
                except Exception:
                    pass

        while asyncio.get_event_loop().time() < deadline:
            session["attempts"] += 1
            attempt_msg = f"Attempt {session['attempts']}: connecting to {host}:{port}…"
            logger.debug(attempt_msg)
            await _emit("attempt", attempt_msg)

            reachable = await self._tcp_probe(host, port)

            if reachable:
                session["status"] = STATUS_CONNECTED
                await _emit("connected", f"SSH port open on {host}:{port}")

                # Update device record to active
                try:
                    db   = self._get_db()
                    repo = DeviceRepository(db)
                    device = Device(
                        id            = device_id,
                        hostname      = params["hostname"],
                        management_ip = params["mgmt_ip"],
                        vendor        = params["vendor"],
                        status        = "up",
                    )
                    repo.save(device)
                    session["status"] = STATUS_REGISTERED
                    await _emit("registered", f"Device '{params['hostname']}' registered in inventory (id={device_id})")
                except Exception as exc:
                    logger.exception("Failed to update device record: %s", exc)
                    await _emit("registered", f"Device reachable but DB update failed: {exc}")

                return  # success — exit loop

            await asyncio.sleep(_POLL_INTERVAL_SEC)

        # Deadline exceeded
        session["status"] = STATUS_FAILED
        await _emit("timeout", f"Timed out after {timeout_minutes} minutes — device did not respond on {host}:{port}")

    # ------------------------------------------------------------------
    # TCP probe
    # ------------------------------------------------------------------

    @staticmethod
    async def _tcp_probe(host: str, port: int) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=_TCP_TIMEOUT_SEC,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False


# Module-level singleton
_poller: Optional[ConnectivityPoller] = None


def get_poller() -> ConnectivityPoller:
    global _poller
    if _poller is None:
        _poller = ConnectivityPoller()
    return _poller
