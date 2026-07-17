"""Shared authentication checks for HTTP routes protected by the install token."""

from __future__ import annotations

import secrets

from fastapi import HTTPException

from glc.config import get_or_create_install_token, install_tenant_id


def require_install_token(authorization: str | None) -> str:
    """Validate the bearer credential and return its authenticated tenant scope."""
    expected = get_or_create_install_token()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token (Authorization: Bearer <install_token>)")
    presented = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(403, "install token mismatch")
    return install_tenant_id()
