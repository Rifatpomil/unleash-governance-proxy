"""SQLAlchemy models for governance proxy."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


def _uuid_default():
    return str(uuid.uuid4())


class ChangeRequest(Base):
    """Change request for a feature flag - pending → approved → applied."""

    __tablename__ = "change_requests"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_uuid_default,
    )
    flag_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    tenant: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # Desired state
    desired_changes: Mapped[dict] = mapped_column(JSON, nullable=False)
    environment: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    strategies: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # State machine: pending | approved | applied | rejected
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending", index=True
    )

    created_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_change_requests_flag_status", "flag_key", "status"),
    )


class AuditLog(Base):
    """Append-only audit log for all governance actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False, default="flag")
    resource_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    before_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Hash chain: prev_hash commits to the previous row's row_hash. Tampering with
    # any past row invalidates every subsequent hash — detectable by verify_chain().
    prev_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    row_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor", "created_at"),
        Index("ix_audit_logs_action_created", "action", "created_at"),
    )


class IdempotencyKey(Base):
    """Idempotency key storage for apply operations."""

    __tablename__ = "idempotency_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    change_request_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    response_status: Mapped[int] = mapped_column(nullable=False)
    response_body: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
