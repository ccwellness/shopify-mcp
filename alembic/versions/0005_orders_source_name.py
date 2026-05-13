"""Add `orders.source_name` to distinguish draft / pos / web / etc.

Shopify exposes the order's `sourceName` on every order — `web` for
online-store checkouts, `shopify_draft_order` for orders staff
created in the admin (and then often discounted to $0 as comps /
replacements), `pos`, `mobile_app`, `shopify_io`, etc. Capturing it
lets the dashboard flag draft / comp orders that distort revenue
math, and lets reporting filter them out.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("source_name", sa.Text, nullable=True))
    # Index keeps "show me all draft orders" / "exclude drafts from revenue"
    # filters fast at scale.
    op.create_index("ix_orders_source_name", "orders", ["source_name"])


def downgrade() -> None:
    op.drop_index("ix_orders_source_name", table_name="orders")
    op.drop_column("orders", "source_name")
