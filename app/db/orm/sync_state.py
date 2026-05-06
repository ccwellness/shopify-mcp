"""ORM model for `sync_state`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm.base import Base


class SyncStateRowOrm(Base):
    """Renamed from SyncStateRow → SyncStateRowOrm to avoid collision with the domain dataclass."""

    __tablename__ = "sync_state"

    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), primary_key=True)
    resource: Mapped[str] = mapped_column(Text, primary_key=True)
    last_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
    )
