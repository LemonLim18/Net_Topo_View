"""
M15: Authentication REST API
==============================
Endpoints for user login, profile, and user management.

Endpoints:
    POST /auth/login          — authenticate, receive JWT
    GET  /auth/me             — current user info
    GET  /auth/users          — list all users (admin)
    POST /auth/users          — create new user (admin)
    PUT  /auth/users/{id}/role — update role (admin)
    DELETE /auth/users/{id}   — delete user (admin)

    POST /auth/credentials            — store device credential
    GET  /auth/credentials/{device_id} — list creds for device
    DELETE /auth/credentials/{device_id}/{label} — delete cred
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.auth.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    seed_default_admin,
    verify_password,
)
from backend.auth.rbac import require_admin, require_any
from backend.auth.credentials import CredentialVault

logger = logging.getLogger("auth.api")
router = APIRouter(prefix="/auth", tags=["Authentication (M15)"])

_DB_PATH = Path(__file__).resolve().parent.parent / "db" / "netauto.db"
_vault = CredentialVault()


def _connect():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --- Request/Response Models ---

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "auditor"


class UpdateRoleRequest(BaseModel):
    role: str


class StoreCredentialRequest(BaseModel):
    device_id: int
    label: str = "SSH"
    username: str
    password: str


# --- Auth Endpoints ---

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """Authenticate with username/password and receive a JWT."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, is_active FROM users WHERE username = ?",
            (body.username,),
        ).fetchone()

        if not row:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

        if not row["is_active"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

        if not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

        # Update last_login
        conn.execute(
            "UPDATE users SET last_login = datetime('now') WHERE id = ?", (row["id"],)
        )
        conn.commit()

        token = create_access_token(row["id"], row["username"], row["role"])
        return LoginResponse(
            access_token=token,
            user={
                "id": row["id"],
                "username": row["username"],
                "role": row["role"],
                "lastLogin": row["last_login"] or "",
            },
        )
    finally:
        conn.close()


@router.get("/me")
async def get_me(user=Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    if user["user_id"] == 0:
        return user  # anonymous

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, username, role, last_login, created_at FROM users WHERE id = ?",
            (user["user_id"],),
        ).fetchone()
        if not row:
            return user
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "lastLogin": row["last_login"] or "",
            "createdAt": row["created_at"],
        }
    finally:
        conn.close()


# --- User Management (Admin Only) ---

@router.get("/users")
async def list_users(user=Depends(require_admin)):
    """List all registered users."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, username, role, is_active, last_login, created_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/users", status_code=201)
async def create_user(body: CreateUserRequest, user=Depends(require_admin)):
    """Create a new user."""
    if body.role not in ("admin", "operator", "auditor"):
        raise HTTPException(400, "Role must be: admin, operator, or auditor")

    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (body.username,)
        ).fetchone()
        if existing:
            raise HTTPException(409, f"User '{body.username}' already exists")

        hashed = hash_password(body.password)
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (body.username, hashed, body.role),
        )
        conn.commit()
        return {"id": cur.lastrowid, "username": body.username, "role": body.role}
    finally:
        conn.close()


@router.put("/users/{user_id}/role")
async def update_user_role(user_id: int, body: UpdateRoleRequest, user=Depends(require_admin)):
    """Update a user's role."""
    if body.role not in ("admin", "operator", "auditor"):
        raise HTTPException(400, "Role must be: admin, operator, or auditor")

    conn = _connect()
    try:
        existing = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(404, f"User {user_id} not found")

        conn.execute("UPDATE users SET role = ? WHERE id = ?", (body.role, user_id))
        conn.commit()
        return {"id": user_id, "role": body.role}
    finally:
        conn.close()


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, user=Depends(require_admin)):
    """Delete a user. Cannot delete yourself."""
    if user["user_id"] == user_id:
        raise HTTPException(400, "Cannot delete your own account")

    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"User {user_id} not found")
        return {"deleted": user_id}
    finally:
        conn.close()


# --- Credential Vault Endpoints ---

@router.post("/credentials")
async def store_credential(body: StoreCredentialRequest, user=Depends(require_any)):
    """Store an encrypted device credential."""
    row_id = _vault.store(body.label, body.device_id, body.username, body.password)
    return {"id": row_id, "device_id": body.device_id, "label": body.label}


@router.get("/credentials/{device_id}")
async def list_device_credentials(device_id: int, user=Depends(require_any)):
    """List credential entries for a device (metadata only, no passwords)."""
    return _vault.list_credentials(device_id)


@router.delete("/credentials/{device_id}/{label}")
async def delete_credential(device_id: int, label: str, user=Depends(require_any)):
    """Delete a stored credential."""
    deleted = _vault.delete(device_id, label)
    if not deleted:
        raise HTTPException(404, f"No '{label}' credential found for device {device_id}")
    return {"deleted": True}
