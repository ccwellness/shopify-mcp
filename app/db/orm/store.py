"""ORM model for `stores`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm.base import Base


class StoreRow(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    store_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    shop_domain: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    plus: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subscription_provider: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
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
