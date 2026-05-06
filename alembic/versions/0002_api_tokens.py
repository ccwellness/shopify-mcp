"""Add api_tokens table for bearer-token auth (TR-4).

Stores SHA-256-hashed bearer tokens. The plaintext is shown to the
operator once at mint time and never again — the column holds the hash.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TS = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column(
            "store_id",
            sa.BigInteger,
            sa.ForeignKey("stores.id"),
            nullable=True,
        ),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("last_used_at", TS, nullable=True),
    )
    op.create_index("ix_api_tokens_name", "api_tokens", ["name"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_name", table_name="api_tokens")
    op.drop_table("api_tokens")
