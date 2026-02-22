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

    from backend.auth.auth import get_current_user, create_access_token

    @router.get("/protected")
    async def protected(user = Depends(get_current_user)):
        return {"hello": user["username"]}
"""

import logging
import os
import secrets
import sqlite3
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
    global _pwd_context
    if _pwd_context is None:
        from passlib.context import CryptContext
        _pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return _pwd_context


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_KEY_FILE = _HERE / ".vault_key"
_DB_PATH = _HERE.parent / "db" / "netauto.db"

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

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
    return _get_pwd_context().hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return _get_pwd_context().verify(plain, hashed)


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
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    )
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
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
            "role": "admin",  # Default full access until RBAC is enforced
        }

    try:
        payload = decode_token(credentials.credentials)
        return {
            "user_id": int(payload["sub"]),
            "username": payload["username"],
            "role": payload["role"],
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

        # Check if any admin exists
        existing = conn.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()
        if existing:
            conn.close()
            return

        # Seed the default admin
        hashed = hash_password("admin")
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", hashed, "admin"),
        )
        conn.commit()
        conn.close()
        logger.info("Seeded default admin user (admin/admin)")

    except Exception as e:
        logger.error("Failed to seed admin user: %s", e)
