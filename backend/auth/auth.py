"""
M15: Authentication Core
========================
JWT token management, password hashing (bcrypt), and FastAPI
dependency for extracting the current user from request headers.

Security model:
- Passwords stored as bcrypt hashes (never plaintext)
- JWT access tokens signed with HS256, configurable expiry
- Default admin account seeded on first startup
- Secret key read from env VAULT_KEY, or auto-generated to file

Usage::

    from auth.auth import get_current_user, create_access_token

    @router.get("/protected")
    async def protected(user = Depends(get_current_user)):
        return {"hello": user["username"]}
"""

import logging
import os
import secrets
import sqlite3
import bcrypt
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("auth")

# ---------------------------------------------------------------------------
# Lazy imports — these are heavy, so only import when first needed
# ---------------------------------------------------------------------------
_jwt = None
_pwd_context = None


def _get_jwt():
    global _jwt
    if _jwt is None:
        from jose import jwt as _jose_jwt
        _jwt = _jose_jwt
    return _jwt


def _get_pwd_context():
    # Deprecated in favor of direct bcrypt usage
    return None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_KEY_FILE = _HERE / ".vault_key"
_DB_PATH = _HERE.parent / "db" / "netauto.db"

ALGORITHM = "HS256"
# How long access tokens are valid for.
#
# If <= 0, we omit the `exp` claim entirely (token does not expire).
# This is convenient for long-lived WebSocket sessions like the SSH terminal.
ACCESS_TOKEN_EXPIRE_HOURS = int(os.environ.get("ACCESS_TOKEN_EXPIRE_HOURS", "0"))

# Ensure .vault_key is gitignored
_gitignore_path = _HERE / ".gitignore"
if not _gitignore_path.exists():
    _gitignore_path.write_text(".vault_key\n")


def _get_secret_key() -> str:
    """
    Load the signing key from environment or file.
    
    Priority:
    1. VAULT_KEY environment variable (production)
    2. .vault_key file (development — auto-generated)
    """
    env_key = os.environ.get("VAULT_KEY")
    if env_key:
        return env_key

    if _KEY_FILE.exists():
        return _KEY_FILE.read_text().strip()

    # Auto-generate a 256-bit key
    key = secrets.token_urlsafe(32)
    _KEY_FILE.write_text(key)
    logger.info("Generated new vault key at %s", _KEY_FILE)
    return key


SECRET_KEY = _get_secret_key()

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def normalize_role(role: Optional[str]) -> str:
    """Normalize legacy roles to the current RBAC vocabulary."""
    value = str(role or "").strip().lower()
    if value in {"operator", "engineer"}:
        return "engineer"
    if value in {"auditor", "viewer"}:
        return "auditor"
    if value == "admin":
        return "admin"
    return value or "auditor"


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------


def create_access_token(
    user_id: int,
    username: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT access token."""
    jwt = _get_jwt()
    now = datetime.now(timezone.utc)
    # If expires_delta is not provided, use configured TTL.
    # If TTL <= 0, we omit `exp` so the token does not expire.
    if expires_delta is not None:
        expire_delta = expires_delta
    elif ACCESS_TOKEN_EXPIRE_HOURS > 0:
        expire_delta = timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    else:
        expire_delta = None

    payload = {
        "sub": str(user_id),
        "username": username,
        "role": normalize_role(role),
        "iat": now,
    }
    if expire_delta is not None:
        payload["exp"] = now + expire_delta
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT. Raises on expiry or invalid signature."""
    jwt = _get_jwt()
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# ---------------------------------------------------------------------------
# FastAPI security dependency
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Dict[str, Any]:
    """
    FastAPI dependency that extracts and validates the JWT from the
    Authorization header. Returns the decoded user payload.

    If no token is provided, returns a default anonymous user to
    preserve backward compatibility with existing unauthenticated
    endpoints.
    """
    if credentials is None:
        # No auth header — return anonymous user for backward compatibility
        return {
            "user_id": 0,
            "username": "anonymous",
            "role": "viewer",
        }

    try:
        payload = decode_token(credentials.credentials)
        return {
            "user_id": int(payload["sub"]),
            "username": payload["username"],
            "role": normalize_role(payload.get("role")),
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Default admin seeding
# ---------------------------------------------------------------------------


def seed_default_admin() -> None:
    """
    Create a default 'admin' user on first startup if the users table
    is empty. Password is bcrypt-hashed (never stored in plaintext).
    """
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        # Check if users table exists
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if not table_check:
            logger.warning("Users table not found — skipping admin seeding")
            conn.close()
            return

        # Seed the default admin account when missing.
        existing_admin = conn.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()
        if not existing_admin:
            hashed = hash_password("admin")
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("admin", hashed, "admin"),
            )
            logger.info("Seeded default admin user (admin/admin)")

        # Seed a default engineer account for RBAC onboarding flows.
        existing_engineer = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            ("engineer",),
        ).fetchone()
        if not existing_engineer:
            hashed = hash_password("engineer")
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("engineer", hashed, "engineer"),
            )
            logger.info("Seeded default engineer user (engineer/engineer)")
        conn.commit()
        conn.close()

    except Exception as e:
        logger.error("Failed to seed admin user: %s", e)
