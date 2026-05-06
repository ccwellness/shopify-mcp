"""ORM model for `webhook_events_log` (TR-14).

Stored separately from the aggregate ORM models because webhook events
are an operational/forensic log, not domain state. Reads are rare and
ad-hoc; writes are append-only.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm.base import Base


class WebhookEventRow(Base):
    __tablename__ = "webhook_events_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    shopify_webhook_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
    hmac_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload_compressed: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_size: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_status: Mapped[str] = mapped_column(Text, nullable=False, default="received")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
