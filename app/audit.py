"""Audit logging — append-only Postgres rows with a SHA-256 hash chain.

Why the chain:
- Without it, "immutable audit log" is aspirational: any DB role that can UPDATE
  the table can rewrite history silently. Pair DB-side `REVOKE UPDATE,DELETE`
  (recommended) with this chain so tampering becomes *detectable* even if the
  revoke is bypassed.
- Each row's `row_hash` = SHA-256 over (prev_hash || canonical(payload)). The
  first row uses a 64-char zero seed as prev_hash. Verifiers walk the chain in
  order and compare recomputed hashes — any divergence pinpoints the first
  tampered row.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuditLog

GENESIS_HASH = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    """Stable JSON serialization for hashing (sorted keys, no whitespace drift)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _compute_row_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(b"|")
    h.update(_canonical(payload).encode("utf-8"))
    return h.hexdigest()


def _latest_hash(db: Session) -> str:
    row = (
        db.query(AuditLog.row_hash)
        .filter(AuditLog.row_hash.isnot(None))
        .order_by(AuditLog.id.desc())
        .first()
    )
    if row and row[0]:
        return row[0]
    return GENESIS_HASH


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
    """Append an audit row; when the hash chain is enabled, extend it atomically."""
    entry = AuditLog(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_payload=before_payload,
        after_payload=after_payload,
        metadata_=metadata_,
    )

    if get_settings().audit_hash_chain_enabled:
        prev = _latest_hash(db)
        payload = {
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "before_payload": before_payload,
            "after_payload": after_payload,
            "metadata": metadata_,
        }
        entry.prev_hash = prev
        entry.row_hash = _compute_row_hash(prev, payload)

    db.add(entry)
    db.flush()
    return entry


def verify_chain(db: Session, limit: Optional[int] = None) -> dict[str, Any]:
    """Walk the audit chain in order. Returns {ok, checked, first_broken_id?}."""
    q = db.query(AuditLog).order_by(AuditLog.id.asc())
    if limit is not None:
        q = q.limit(limit)

    prev = GENESIS_HASH
    checked = 0
    for row in q.yield_per(500):
        checked += 1
        if row.row_hash is None and row.prev_hash is None:
            # Row pre-dates the chain (legacy data). Skip without breaking.
            continue
        payload = {
            "actor": row.actor,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "before_payload": row.before_payload,
            "after_payload": row.after_payload,
            "metadata": row.metadata_,
        }
        expected = _compute_row_hash(prev, payload)
        if row.prev_hash != prev or row.row_hash != expected:
            return {
                "ok": False,
                "checked": checked,
                "first_broken_id": row.id,
                "expected_prev_hash": prev,
                "actual_prev_hash": row.prev_hash,
                "expected_row_hash": expected,
                "actual_row_hash": row.row_hash,
            }
        prev = row.row_hash
    return {"ok": True, "checked": checked}
