"""ORM model for `subscription_contracts`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm.base import Base


class SubscriptionContractRow(Base):
    __tablename__ = "subscription_contracts"
    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "provider",
            "provider_contract_id",
            name="uq_subscription_contracts_provider_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), nullable=False)
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_contract_id: Mapped[str] = mapped_column(Text, nullable=False)
    gid: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    next_billing_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    frequency_interval: Mapped[str | None] = mapped_column(Text, nullable=True)
    frequency_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
