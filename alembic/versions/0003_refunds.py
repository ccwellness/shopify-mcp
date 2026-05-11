"""Add refunds table for accurate revenue accounting.

Refunds are first-class objects on Shopify with their own GIDs, partial
amounts, and timestamps. We store one row per refund so revenue
reporting can deduct refunds in the window they happened, independent
of the order's processed_at.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MONEY = sa.Numeric(19, 4)
TS = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "refunds",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "order_id",
            sa.BigInteger,
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gid", sa.Text, nullable=False),
        sa.Column("legacy_id", sa.BigInteger, nullable=False),
        sa.Column("amount", MONEY, nullable=False, server_default=sa.text("0")),
        sa.Column("currency_code", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("ingested_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("store_id", "gid", name="uq_refunds_store_gid"),
    )
    op.create_index("ix_refunds_store_created", "refunds", ["store_id", "created_at"])
    op.create_index("ix_refunds_order", "refunds", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_refunds_order", table_name="refunds")
    op.drop_index("ix_refunds_store_created", table_name="refunds")
    op.drop_table("refunds")
