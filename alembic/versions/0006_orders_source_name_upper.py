"""Backfill `orders.source_name` to UPPER.

Shopify ships casing inconsistencies in `sourceName` — the same store's
order feed contains both `tiktok` and `TikTok`. We normalize to UPPER
at write time (in the bulk + webhook normalizers); this migration
brings existing rows in line so filters / equality checks don't need
to be case-insensitive.

No-op on rows where `source_name` is NULL.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE orders SET source_name = upper(source_name) "
            "WHERE source_name IS NOT NULL AND source_name <> upper(source_name)"
        )
    )


def downgrade() -> None:
    # Lossy direction (we can't recover the original mixed casing). Intentional
    # no-op — the canonical form is upper, and the bulk feed will re-supply
    # the original casing on the next sync if you ever need to inspect it.
    pass
