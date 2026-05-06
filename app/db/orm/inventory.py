"""ORM models for the Inventory aggregate (`inventory_items` + `inventory_levels`)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.orm.base import Base


class InventoryItemRow(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (UniqueConstraint("store_id", "gid", name="uq_inventory_items_store_gid"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), nullable=False)
    variant_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("variants.id", ondelete="SET NULL"), nullable=True
    )
    gid: Mapped[str] = mapped_column(Text, nullable=False)
    legacy_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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

    levels: Mapped[list[InventoryLevelRow]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class InventoryLevelRow(Base):
    __tablename__ = "inventory_levels"
    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "inventory_item_id",
            "location_id",
            name="uq_inventory_levels_store_item_location",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), nullable=False)
    inventory_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("locations.id", ondelete="CASCADE"), nullable=False
    )
    available: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_hand: Mapped[int | None] = mapped_column(Integer, nullable=True)
    committed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    incoming: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

    item: Mapped[InventoryItemRow] = relationship(back_populates="levels")
