"""ORM models for `sessions_daily` and `analytics_kpi_daily`."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm.base import Base


class SessionsDayRow(Base):
    __tablename__ = "sessions_daily"

    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), primary_key=True)
    date: Mapped[date_type] = mapped_column(Date, primary_key=True)
    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_sales: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    units_sold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="shopifyql")
    pulled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )


class AnalyticsKpiDayRow(Base):
    __tablename__ = "analytics_kpi_daily"

    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), primary_key=True)
    date: Mapped[date_type] = mapped_column(Date, primary_key=True)
    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    conversion_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    aov: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
