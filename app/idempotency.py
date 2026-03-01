"""Idempotency for apply operations."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import IdempotencyKey


IDEMPOTENCY_HEADER = "Idempotency-Key"


def get_idempotency_key(request: Request) -> Optional[str]:
    """Extract Idempotency-Key from request headers."""
    return request.headers.get(IDEMPOTENCY_HEADER)


def require_idempotency_key(request: Request) -> str:
    """Require Idempotency-Key header, raise 400 if missing."""
    key = get_idempotency_key(request)
    if not key or not key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required header: {IDEMPOTENCY_HEADER}",
        )
    if len(key) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{IDEMPOTENCY_HEADER} must be at most 255 characters",
        )
    return key.strip()


def get_existing_response(
    db: Session,
    key: str,
) -> Optional[tuple[int, Optional[dict]]]:
    """
    Return (status_code, response_body) if key exists and not expired.
    """
    settings = get_settings()
    now = datetime.utcnow()
    row = (
        db.query(IdempotencyKey)
        .filter(
            and_(
                IdempotencyKey.key == key,
                IdempotencyKey.expires_at > now,
            )
        )
        .first()
    )
    if row:
        return (row.response_status, row.response_body)
    return None


def store_idempotency_response(
    db: Session,
    key: str,
    change_request_id: str,
    status_code: int,
    response_body: Optional[dict],
) -> None:
    """Store idempotency key with response for deduplication."""
    settings = get_settings()
    expires_at = datetime.utcnow() + timedelta(
        seconds=settings.idempotency_ttl_seconds
    )
    row = IdempotencyKey(
        key=key,
        change_request_id=change_request_id,
        response_status=status_code,
        response_body=response_body,
        expires_at=expires_at,
    )
    db.add(row)
