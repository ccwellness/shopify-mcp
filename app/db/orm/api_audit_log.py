"""ORM model for `api_audit_log` (TR-6).

One row per inbound API or MCP call. `params_sanitized` is JSONB so
queries can filter on individual fields without parsing strings.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm.base import Base


class ApiAuditLogRow(Base):
    __tablename__ = "api_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
    caller_identity: Mapped[str] = mapped_column(Text, nullable=False)
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), nullable=True)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    route_or_tool: Mapped[str] = mapped_column(Text, nullable=False)
    params_sanitized: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
