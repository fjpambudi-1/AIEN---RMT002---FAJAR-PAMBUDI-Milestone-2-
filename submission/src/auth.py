"""Server-side demo bearer token verification."""

from __future__ import annotations

import hmac
from fastapi import Depends, Header, HTTPException, status

from src.config import Settings, get_settings


def verify_bearer_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Verify the demo bearer token and return the server-side user identity.

    Client-supplied user_id values are never used for authorization.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token otorisasi tidak ditemukan.",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Format header Authorization harus Bearer <token>.",
        )

    candidate = token.strip()
    if settings.demo_token_a and hmac.compare_digest(candidate, settings.demo_token_a):
        return "user_a"
    if settings.demo_token_b and hmac.compare_digest(candidate, settings.demo_token_b):
        return "user_b"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token demo tidak valid.",
    )
