"""JWT authentication. Supports HS256 (shared secret) and RS256/ES256 via JWKS.

When `JWT_JWKS_URL` is set, we fetch the issuer's public key set, look up the
signing key by the token's `kid` header, and verify asymmetrically. Keys are
cached with a TTL and refreshed on `kid` miss (handles key rotation without a
restart). When `JWT_JWKS_URL` is unset we fall back to HS256 against `jwt_secret`.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)

security = HTTPBearer(auto_error=True)


_jwks_cache: dict[str, Any] = {"keys": {}, "fetched_at": 0.0}


def _fetch_jwks(url: str) -> dict[str, dict[str, Any]]:
    """Fetch JWKS and index by kid. Small, synchronous, blocking — called at most
    every `jwt_jwks_cache_seconds` so it does not belong in the hot path."""
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    keys = {k["kid"]: k for k in resp.json().get("keys", []) if "kid" in k}
    logger.info("jwks_fetched", url=url, key_count=len(keys))
    return keys


def _get_signing_key(kid: str) -> Optional[dict[str, Any]]:
    """Return the JWK for `kid`, refreshing the cache if needed."""
    settings = get_settings()
    url = settings.jwt_jwks_url
    if not url:
        return None

    now = time.time()
    stale = now - _jwks_cache["fetched_at"] > settings.jwt_jwks_cache_seconds
    if stale or kid not in _jwks_cache["keys"]:
        try:
            _jwks_cache["keys"] = _fetch_jwks(url)
            _jwks_cache["fetched_at"] = now
        except Exception as e:
            logger.error("jwks_fetch_failed", error=str(e), url=url)
            # Fall through — maybe the cached key still works.
    return _jwks_cache["keys"].get(kid)


def decode_jwt(token: str) -> dict:
    settings = get_settings()
    try:
        if settings.jwt_jwks_url:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if not kid:
                raise JWTError("token missing kid header")
            key = _get_signing_key(kid)
            if not key:
                raise JWTError(f"unknown kid: {kid}")
            return jwt.decode(
                token,
                key,
                algorithms=[settings.jwt_algorithm, header.get("alg", "RS256")],
                audience=settings.jwt_audience or None,
                issuer=settings.jwt_issuer or None,
            )
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience or None,
            issuer=settings.jwt_issuer or None,
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    payload = decode_jwt(credentials.credentials)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "sub": sub,
        "email": payload.get("email"),
        "tenant": payload.get("tenant"),
        "raw": payload,
    }


def reset_jwks_cache() -> None:
    """Test hook."""
    _jwks_cache["keys"] = {}
    _jwks_cache["fetched_at"] = 0.0
