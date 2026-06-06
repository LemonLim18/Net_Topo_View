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

from auth.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    normalize_role,
    seed_default_admin,
    verify_password,
)
from auth.rbac import require_admin, require_any
from auth.credentials import CredentialVault

logger = logging.getLogger("auth.api")
router = APIRouter(prefix="/auth", tags=["Authentication (M15)"])

_DB_PATH = Path(__file__).resolve().parent.parent / "db" / "netauto.db"
_vault = CredentialVault()
_ALLOWED_ROLES = {"admin", "engineer", "operator", "auditor"}


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
    role: str = "engineer"


class SignupRequest(BaseModel):
    username: str
    password: str
    full_name: str
    email: str
    avatar: Optional[str] = None
    security_question: str
    security_answer: str


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    avatar: Optional[str] = None
    security_question: Optional[str] = None
    security_answer: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None


class SecurityQuestionRequest(BaseModel):
    username: str


class ResetPasswordRequest(BaseModel):
    username: str
    security_answer: str
    new_password: str


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
            "SELECT id, username, password_hash, role, is_active, last_login, full_name, email, avatar FROM users WHERE username = ?",
            (body.username.strip(),),
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
                "role": normalize_role(row["role"]),
                "lastLogin": row["last_login"] or "",
                "fullName": row["full_name"] or "",
                "email": row["email"] or "",
                "avatar": row["avatar"] or "",
            },
        )
    finally:
        conn.close()


@router.post("/signup", response_model=LoginResponse, status_code=201)
async def signup(body: SignupRequest):
    """Self-service signup for engineer accounts."""
    username = body.username.strip()
    full_name = body.full_name.strip()
    email = body.email.strip()
    question = body.security_question.strip()
    answer = body.security_answer.strip().lower()

    if not username:
        raise HTTPException(400, "Username is required")
    if len(username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters")
    if not full_name:
        raise HTTPException(400, "Full Name is required")
    if not email or "@" not in email:
        raise HTTPException(400, "A valid Email Address is required")
    if not question or not answer:
        raise HTTPException(400, "Security question and answer are required")
    if len(body.password) < 6 or not any(c.isalpha() for c in body.password) or not any(c.isdigit() for c in body.password):
        raise HTTPException(400, "Password must be at least 6 characters and contain both letters and digits")

    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing:
            raise HTTPException(409, f"User '{username}' already exists")

        hashed = hash_password(body.password)
        ans_hashed = hash_password(answer)
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, full_name, email, avatar, security_question, security_answer_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (username, hashed, "engineer", full_name, email, body.avatar, question, ans_hashed),
        )
        conn.commit()
        token = create_access_token(cur.lastrowid, username, "engineer")
        return LoginResponse(
            access_token=token,
            user={
                "id": cur.lastrowid,
                "username": username,
                "role": "engineer",
                "lastLogin": "",
                "fullName": full_name,
                "email": email,
                "avatar": body.avatar or "",
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
            "SELECT id, username, role, last_login, created_at, full_name, email, avatar FROM users WHERE id = ?",
            (user["user_id"],),
        ).fetchone()
        if not row:
            return user
        return {
            "id": row["id"],
            "username": row["username"],
            "role": normalize_role(row["role"]),
            "lastLogin": row["last_login"] or "",
            "createdAt": row["created_at"],
            "fullName": row["full_name"] or "",
            "email": row["email"] or "",
            "avatar": row["avatar"] or "",
        }
    finally:
        conn.close()

@router.get("/users")
async def list_users(user=Depends(require_admin)):
    """List all registered users."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, username, role, is_active, last_login, created_at FROM users ORDER BY id"
        ).fetchall()
        return [
            {
                **dict(r),
                "role": normalize_role(r["role"]),
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.post("/users", status_code=201)
async def create_user(body: CreateUserRequest, user=Depends(require_admin)):
    """Create a new user."""
    requested_role = normalize_role(body.role)
    if requested_role not in {"admin", "engineer", "auditor"}:
        raise HTTPException(400, "Role must be: admin, engineer, or auditor")

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
            (body.username, hashed, requested_role),
        )
        conn.commit()
        return {"id": cur.lastrowid, "username": body.username, "role": requested_role}
    finally:
        conn.close()


@router.put("/users/{user_id}/role")
async def update_user_role(user_id: int, body: UpdateRoleRequest, user=Depends(require_admin)):
    """Update a user's role."""
    requested_role = normalize_role(body.role)
    if requested_role not in {"admin", "engineer", "auditor"}:
        raise HTTPException(400, "Role must be: admin, engineer, or auditor")

    conn = _connect()
    try:
        existing = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(404, f"User {user_id} not found")

        conn.execute("UPDATE users SET role = ? WHERE id = ?", (requested_role, user_id))
        conn.commit()
        return {"id": user_id, "role": requested_role}
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


@router.put("/me/profile")
async def update_profile(body: UpdateProfileRequest, user=Depends(get_current_user)):
    """Update current user's profile information."""
    if user["user_id"] == 0:
        raise HTTPException(401, "Anonymous user cannot update profile")

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?",
            (user["user_id"],)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")

        # If changing password, verify current password first
        if body.new_password:
            if not body.current_password:
                raise HTTPException(400, "Current password is required to set a new password")
            if not verify_password(body.current_password, row["password_hash"]):
                raise HTTPException(400, "Incorrect current password")
            if len(body.new_password) < 6 or not any(c.isalpha() for c in body.new_password) or not any(c.isdigit() for c in body.new_password):
                raise HTTPException(400, "New password must be at least 6 characters long and contain both letters and numbers")

        updates = []
        params = []
        
        if body.full_name is not None:
            updates.append("full_name = ?")
            params.append(body.full_name.strip())
        if body.email is not None:
            if body.email.strip() and "@" not in body.email:
                raise HTTPException(400, "Invalid email format")
            updates.append("email = ?")
            params.append(body.email.strip())
        if body.avatar is not None:
            updates.append("avatar = ?")
            params.append(body.avatar)
        if body.security_question is not None:
            updates.append("security_question = ?")
            params.append(body.security_question.strip())
        if body.security_answer is not None:
            ans_hashed = hash_password(body.security_answer.strip().lower())
            updates.append("security_answer_hash = ?")
            params.append(ans_hashed)
        if body.new_password:
            new_hashed = hash_password(body.new_password)
            updates.append("password_hash = ?")
            params.append(new_hashed)

        if not updates:
            raise HTTPException(400, "No update fields provided")

        params.append(user["user_id"])
        conn.execute(
            f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
            tuple(params)
        )
        conn.commit()
        return {"status": "success", "message": "Profile updated successfully"}
    finally:
        conn.close()


@router.post("/reset-password/question")
async def get_reset_question(body: SecurityQuestionRequest):
    """Retrieve security question for a user."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT security_question FROM users WHERE username = ?",
            (body.username.strip(),)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        if not row["security_question"]:
            raise HTTPException(400, "No security question configured for this user")
        return {"question": row["security_question"]}
    finally:
        conn.close()


@router.post("/reset-password/reset")
async def reset_password_endpoint(body: ResetPasswordRequest):
    """Reset user password using security answer."""
    username = body.username.strip()
    answer = body.security_answer.strip().lower()
    
    if len(body.new_password) < 6 or not any(c.isalpha() for c in body.new_password) or not any(c.isdigit() for c in body.new_password):
        raise HTTPException(400, "Password must be at least 6 characters long and contain both letters and numbers")

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, security_answer_hash FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        if not row["security_answer_hash"]:
            raise HTTPException(400, "No security question answer configured for this user")

        if not verify_password(answer, row["security_answer_hash"]):
            raise HTTPException(400, "Incorrect answer to security question")

        new_hash = hash_password(body.new_password)
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, row["id"])
        )
        conn.commit()
        return {"status": "success", "message": "Password reset successful"}
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
