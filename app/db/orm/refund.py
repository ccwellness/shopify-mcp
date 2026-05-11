"""ORM model for `refunds`.

One row per Shopify refund (multiple per order possible — partial
refunds are common). Pulled by a dedicated `sync_refunds` job, not the
bulk-orders flow, because `Order.refunds` is a plain list and Shopify
bulk operations only emit connections.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm.base import Base


class RefundRow(Base):
    __tablename__ = "refunds"
    __table_args__ = (UniqueConstraint("store_id", "gid", name="uq_refunds_store_gid"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    gid: Mapped[str] = mapped_column(Text, nullable=False)
    legacy_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False, default=Decimal("0"))
    currency_code: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
