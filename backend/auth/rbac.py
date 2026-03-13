"""
M15: Role-Based Access Control (RBAC)
======================================
FastAPI dependency factory that checks user roles.

Roles (ordered by privilege):
    - admin    → full access
    - operator → read + write operations
    - auditor  → read-only access

Temporary testing bypass: set env AUTH_BYPASS_ROLES=1 to allow any authenticated
user to pass role checks. Remove or set to 0 for production.

Usage::

    from auth.rbac import require_role

    @router.post("/deploy")
    async def deploy(user = Depends(require_role("operator", "admin"))):
        ...
"""

import os
from typing import Any, Dict, Tuple

from fastapi import Depends, HTTPException, status

from auth.auth import get_current_user


def require_role(*allowed_roles: str):
    """
    Return a FastAPI dependency that ensures the authenticated user
    has one of the specified roles.

    Args:
        *allowed_roles: Role names permitted for this endpoint.
                        E.g. ``require_role("admin", "operator")``

    Returns:
        A dependency function that returns the user dict or raises 403.
    """

    async def _check(user: Dict[str, Any] = Depends(get_current_user)):
        if os.environ.get("AUTH_BYPASS_ROLES", "").strip() in ("1", "true", "yes"):
            return user
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user['role']}' is not authorized. Required: {', '.join(allowed_roles)}",
            )
        return user

    return _check


# Convenience shortcuts
require_admin = require_role("admin")
require_operator = require_role("admin", "operator")
require_any = require_role("admin", "operator", "auditor")
