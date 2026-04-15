"""Audit log read endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.auth import get_current_user
from app.db import get_db
from app.db.models import AuditLog
from app.models import AuditLogListResponse

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("", response_model=AuditLogListResponse)
def list_audit_logs(
    actor: Optional[str] = Query(None, description="Filter by actor"),
    action: Optional[str] = Query(None, description="Filter by action"),
    resource_id: Optional[str] = Query(None, description="Filter by resource ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List audit log entries with optional filters and pagination."""
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if actor:
        q = q.filter(AuditLog.actor == actor)
    if action:
        q = q.filter(AuditLog.action == action)
    if resource_id:
        q = q.filter(AuditLog.resource_id == resource_id)

    total = q.count()
    entries = q.offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "entries": [
            {
                "id": e.id,
                "actor": e.actor,
                "action": e.action,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "before_payload": e.before_payload,
                "after_payload": e.after_payload,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ],
    }


@router.get("/verify")
def verify_audit_chain(
    limit: Optional[int] = Query(None, ge=1, le=100_000),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verify the SHA-256 hash chain. Returns {ok, checked, first_broken_id?}.

    Running this periodically (or exposing it to SOC/compliance tooling) turns
    "immutable log" from a claim into a verifiable property.
    """
    return verify_chain(db, limit=limit)
