"""Audit logging - append-only to Postgres."""

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def write_audit(
    db: Session,
    actor: str,
    action: str,
    resource_type: str = "flag",
    resource_id: Optional[str] = None,
    before_payload: Optional[dict] = None,
    after_payload: Optional[dict] = None,
    metadata_: Optional[dict] = None,
) -> AuditLog:
    """Append an immutable audit record."""
    entry = AuditLog(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_payload=before_payload,
        after_payload=after_payload,
        metadata_=metadata_,
    )
    db.add(entry)
    db.flush()
    return entry
