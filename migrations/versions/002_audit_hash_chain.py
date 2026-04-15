"""Add hash-chain columns to audit_logs.

Revision ID: 002
Revises: 001
Create Date: 2026-04-14

`prev_hash` and `row_hash` are nullable so pre-chain rows remain valid.
New inserts populate both. verify_chain() walks the chain in id order.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("row_hash", sa.String(64), nullable=True))
    op.create_index("ix_audit_logs_row_hash", "audit_logs", ["row_hash"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_row_hash", table_name="audit_logs")
    op.drop_column("audit_logs", "row_hash")
    op.drop_column("audit_logs", "prev_hash")
