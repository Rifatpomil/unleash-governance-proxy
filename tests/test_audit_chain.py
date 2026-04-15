"""Tests for the audit hash chain.

Properties we care about:
- Consecutive writes link: row N+1's prev_hash == row N's row_hash.
- Mutating any past row makes verify_chain flag the *first* broken row.
- Legacy rows (null hash fields) do not break the verifier.
"""

from __future__ import annotations

from app.audit import GENESIS_HASH, verify_chain, write_audit
from app.db.models import AuditLog


def test_chain_links_across_writes(db_session):
    a = write_audit(db_session, actor="alice", action="x", resource_id="1")
    b = write_audit(db_session, actor="alice", action="x", resource_id="2")
    c = write_audit(db_session, actor="alice", action="x", resource_id="3")
    db_session.commit()

    assert a.prev_hash == GENESIS_HASH
    assert b.prev_hash == a.row_hash
    assert c.prev_hash == b.row_hash
    assert verify_chain(db_session) == {"ok": True, "checked": 3}


def test_chain_detects_tamper(db_session):
    write_audit(db_session, actor="alice", action="x", resource_id="1")
    tampered = write_audit(db_session, actor="alice", action="x", resource_id="2")
    write_audit(db_session, actor="alice", action="x", resource_id="3")
    db_session.commit()

    # Simulate an attacker editing a past row after the fact.
    tampered.actor = "mallory"
    db_session.commit()

    result = verify_chain(db_session)
    assert result["ok"] is False
    assert result["first_broken_id"] == tampered.id


def test_verify_ignores_legacy_null_rows(db_session):
    """Pre-chain rows (hash columns NULL) must not break verification."""
    db_session.add(AuditLog(actor="legacy", action="x", resource_type="flag"))
    db_session.commit()
    write_audit(db_session, actor="alice", action="x", resource_id="1")
    db_session.commit()

    assert verify_chain(db_session)["ok"] is True
