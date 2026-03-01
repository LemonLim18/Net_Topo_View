"""
Phase 3 — SDN Controller Management API.

Full CRUD for SDN controllers plus live sync/health-check.

Endpoints (mounted at /api/v1):
    GET    /sdn/controllers           List all registered controllers
    POST   /sdn/controllers           Register a new controller
    GET    /sdn/controllers/{id}      Get controller detail + managed devices
    PUT    /sdn/controllers/{id}      Update controller settings
    DELETE /sdn/controllers/{id}      Remove a controller
    POST   /sdn/controllers/{id}/sync Trigger live sync (topology pull)
    GET    /sdn/controllers/{id}/topology  Get topology from controller
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("sdn.api")

router = APIRouter(prefix="/sdn", tags=["SDN Controllers"])

# DI globals
_db = None

try:
    from auth.auth import get_current_user as _auth_dep
except ImportError:
    def _auth_dep():  # type: ignore
        return {"username": "anonymous", "role": "admin"}


def setup_sdn(db_manager) -> None:
    """Inject database manager at app startup."""
    global _db
    _db = db_manager
    logger.info("SDN controller management module ready")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ControllerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    controller_type: str = Field(..., description="opendaylight | onos")
    url: str = Field(..., min_length=1)
    username: Optional[str] = None
    password: Optional[str] = None
    verify_ssl: bool = False


class ControllerUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    verify_ssl: Optional[bool] = None


class ControllerResponse(BaseModel):
    id: int
    name: str
    controller_type: str
    url: str
    username: Optional[str] = None
    verify_ssl: bool = False
    status: str = "offline"
    managed_device_count: int = 0
    last_synced: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ControllerDetail(ControllerResponse):
    managed_devices: List[Dict[str, Any]] = []
    topology_links: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_db():
    if _db is None:
        raise HTTPException(status_code=503, detail="SDN module not initialised")


def _row_to_dict(row) -> Dict[str, Any]:
    if row is None:
        return {}
    return dict(row)


def _encrypt_password(plain: str) -> str:
    """Optionally encrypt via Fernet. Falls back to base64."""
    try:
        from auth.credentials import CredentialVault
        v = CredentialVault()
        return v._fernet.encrypt(plain.encode()).decode()
    except Exception:
        import base64
        return base64.b64encode(plain.encode()).decode()


def _decrypt_password(enc: str) -> str:
    """Decrypt. Falls back to base64 decode."""
    try:
        from auth.credentials import CredentialVault
        v = CredentialVault()
        return v._fernet.decrypt(enc.encode()).decode()
    except Exception:
        import base64
        try:
            return base64.b64decode(enc.encode()).decode()
        except Exception:
            return ""


def _get_driver(ctrl: Dict[str, Any]):
    """Instantiate the correct SDN driver for a controller record."""
    from drivers.factory import DriverFactory

    password = ""
    if ctrl.get("password_enc"):
        password = _decrypt_password(ctrl["password_enc"])

    return DriverFactory.create(
        driver_type=ctrl["controller_type"],
        controller_url=ctrl["url"],
        username=ctrl.get("username") or "admin",
        password=password,
        verify_ssl=bool(ctrl.get("verify_ssl", False)),
    )


# ---------------------------------------------------------------------------
# CRUD Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/controllers",
    response_model=List[ControllerResponse],
    summary="List all registered SDN controllers",
)
async def list_controllers(
    current_user: dict = Depends(_auth_dep),
):
    _check_db()
    with _db._connect() as conn:
        rows = conn.execute(
            "SELECT id, name, controller_type, url, username, verify_ssl, "
            "status, managed_device_count, last_synced, created_at, updated_at "
            "FROM sdn_controllers ORDER BY name"
        ).fetchall()
    return [ControllerResponse(**_row_to_dict(r)) for r in rows]


@router.post(
    "/controllers",
    response_model=ControllerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new SDN controller",
)
async def create_controller(
    body: ControllerCreate,
    current_user: dict = Depends(_auth_dep),
):
    _check_db()

    valid_types = {"opendaylight", "odl", "onos"}
    if body.controller_type.lower() not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid controller_type. Must be one of: {sorted(valid_types)}")

    password_enc = _encrypt_password(body.password) if body.password else None

    with _db._connect() as conn:
        # Check for duplicate name
        existing = conn.execute(
            "SELECT id FROM sdn_controllers WHERE name = ?", (body.name,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail=f"Controller '{body.name}' already exists")

        cursor = conn.execute(
            "INSERT INTO sdn_controllers (name, controller_type, url, username, password_enc, verify_ssl) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (body.name, body.controller_type.lower(), body.url, body.username, password_enc, body.verify_ssl),
        )
        ctrl_id = cursor.lastrowid

        row = conn.execute(
            "SELECT id, name, controller_type, url, username, verify_ssl, "
            "status, managed_device_count, last_synced, created_at, updated_at "
            "FROM sdn_controllers WHERE id = ?",
            (ctrl_id,),
        ).fetchone()

    logger.info("user=%s registered SDN controller '%s' (type=%s)", current_user.get("username"), body.name, body.controller_type)
    return ControllerResponse(**_row_to_dict(row))


@router.get(
    "/controllers/{controller_id}",
    response_model=ControllerDetail,
    summary="Get detailed info for one SDN controller",
)
async def get_controller(
    controller_id: int,
    current_user: dict = Depends(_auth_dep),
):
    _check_db()
    with _db._connect() as conn:
        row = conn.execute(
            "SELECT id, name, controller_type, url, username, verify_ssl, "
            "status, managed_device_count, last_synced, created_at, updated_at "
            "FROM sdn_controllers WHERE id = ?",
            (controller_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Controller not found")

    detail = ControllerDetail(**_row_to_dict(row))

    # Attempt live device query (non-blocking failure)
    try:
        ctrl = _row_to_dict(row)
        devices, links = await run_in_threadpool(_sync_controller_live, ctrl)
        detail.managed_devices = devices
        detail.topology_links = links
    except Exception as e:
        logger.debug("Live query for controller %d failed: %s", controller_id, e)

    return detail


@router.put(
    "/controllers/{controller_id}",
    response_model=ControllerResponse,
    summary="Update an SDN controller",
)
async def update_controller(
    controller_id: int,
    body: ControllerUpdate,
    current_user: dict = Depends(_auth_dep),
):
    _check_db()
    with _db._connect() as conn:
        existing = conn.execute("SELECT id FROM sdn_controllers WHERE id = ?", (controller_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Controller not found")

        updates = []
        params = []
        if body.name is not None:
            updates.append("name = ?")
            params.append(body.name)
        if body.url is not None:
            updates.append("url = ?")
            params.append(body.url)
        if body.username is not None:
            updates.append("username = ?")
            params.append(body.username)
        if body.password is not None:
            updates.append("password_enc = ?")
            params.append(_encrypt_password(body.password))
        if body.verify_ssl is not None:
            updates.append("verify_ssl = ?")
            params.append(body.verify_ssl)

        if updates:
            updates.append("updated_at = datetime('now')")
            params.append(controller_id)
            conn.execute(
                f"UPDATE sdn_controllers SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        row = conn.execute(
            "SELECT id, name, controller_type, url, username, verify_ssl, "
            "status, managed_device_count, last_synced, created_at, updated_at "
            "FROM sdn_controllers WHERE id = ?",
            (controller_id,),
        ).fetchone()

    return ControllerResponse(**_row_to_dict(row))


@router.delete(
    "/controllers/{controller_id}",
    summary="Remove an SDN controller",
)
async def delete_controller(
    controller_id: int,
    current_user: dict = Depends(_auth_dep),
):
    _check_db()
    with _db._connect() as conn:
        existing = conn.execute("SELECT name FROM sdn_controllers WHERE id = ?", (controller_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Controller not found")
        conn.execute("DELETE FROM sdn_controllers WHERE id = ?", (controller_id,))

    logger.info("user=%s deleted SDN controller id=%d", current_user.get("username"), controller_id)
    return {"status": "deleted", "controller_id": controller_id}


# ---------------------------------------------------------------------------
# Sync / Topology
# ---------------------------------------------------------------------------

def _sync_controller_live(ctrl: Dict[str, Any]):
    """Connect to controller, fetch managed devices + topology links."""
    driver = _get_driver(ctrl)
    devices = []
    links = []

    try:
        driver.connect()
        if hasattr(driver, "get_managed_devices"):
            raw_devices = driver.get_managed_devices()
            if isinstance(raw_devices, list):
                for d in raw_devices:
                    if hasattr(d, "__dict__"):
                        devices.append({k: str(v) for k, v in d.__dict__.items() if not k.startswith("_")})
                    elif isinstance(d, dict):
                        devices.append(d)
                    else:
                        devices.append({"id": str(d)})

        if hasattr(driver, "get_topology_links"):
            raw_links = driver.get_topology_links()
            if isinstance(raw_links, list):
                for ln in raw_links:
                    if hasattr(ln, "__dict__"):
                        links.append({k: str(v) for k, v in ln.__dict__.items() if not k.startswith("_")})
                    elif isinstance(ln, dict):
                        links.append(ln)
    except Exception as e:
        logger.warning("SDN sync failed for %s: %s", ctrl.get("name"), e)
    finally:
        try:
            driver.disconnect()
        except Exception:
            pass

    return devices, links


@router.post(
    "/controllers/{controller_id}/sync",
    summary="Trigger a live sync with the SDN controller",
)
async def sync_controller(
    controller_id: int,
    current_user: dict = Depends(_auth_dep),
):
    _check_db()
    with _db._connect() as conn:
        row = conn.execute(
            "SELECT * FROM sdn_controllers WHERE id = ?", (controller_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Controller not found")

    ctrl = _row_to_dict(row)

    try:
        devices, links = await run_in_threadpool(_sync_controller_live, ctrl)
        now = datetime.utcnow().isoformat()

        with _db._connect() as conn:
            conn.execute(
                "UPDATE sdn_controllers SET status = 'online', managed_device_count = ?, "
                "last_synced = ?, updated_at = datetime('now') WHERE id = ?",
                (len(devices), now, controller_id),
            )

        return {
            "status": "synced",
            "controller_id": controller_id,
            "managed_device_count": len(devices),
            "topology_links": len(links),
            "devices": devices,
            "links": links,
            "synced_at": now,
        }

    except Exception as e:
        with _db._connect() as conn:
            conn.execute(
                "UPDATE sdn_controllers SET status = 'error', updated_at = datetime('now') WHERE id = ?",
                (controller_id,),
            )
        raise HTTPException(status_code=502, detail=f"Sync failed: {e}")


@router.get(
    "/controllers/{controller_id}/topology",
    summary="Get the topology discovered by an SDN controller",
)
async def get_controller_topology(
    controller_id: int,
    current_user: dict = Depends(_auth_dep),
):
    _check_db()
    with _db._connect() as conn:
        row = conn.execute(
            "SELECT * FROM sdn_controllers WHERE id = ?", (controller_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Controller not found")

    ctrl = _row_to_dict(row)

    try:
        devices, links = await run_in_threadpool(_sync_controller_live, ctrl)
        return {
            "controller_id": controller_id,
            "controller_name": ctrl.get("name"),
            "devices": devices,
            "links": links,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Topology fetch failed: {e}")
