"""Index `order_line_items.product_id` for per-product analytics.

The product detail view runs two queries hot:
  - daily sales-by-day for a single product over the trailing 30 days
  - the 20 most-recent orders containing a single product

Both filter `order_line_items` by `product_id`. The existing indexes
(`ix_order_line_items_order_id`, `ix_order_line_items_store_sku`) don't
help here, so without this index every page load scans the whole 30-day
slice of the line-items table for every product.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_order_line_items_product_id",
        "order_line_items",
        ["product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_order_line_items_product_id", table_name="order_line_items")
