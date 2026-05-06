"""SqlAlchemyApiAuditLogRepository — append-only audit log (TR-6)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.orm.api_audit_log import ApiAuditLogRow
from app.domain.models import ApiAuditLogEntry, ApiAuditLogId, StoreId


def _to_domain(row: ApiAuditLogRow) -> ApiAuditLogEntry:
    return ApiAuditLogEntry(
        id=ApiAuditLogId(row.id),
        ts=row.ts,
        caller_identity=row.caller_identity,
        store_id=StoreId(row.store_id) if row.store_id is not None else None,
        surface=row.surface,
        route_or_tool=row.route_or_tool,
        params_sanitized=row.params_sanitized,
        status_code=row.status_code,
        latency_ms=row.latency_ms,
        request_id=row.request_id,
    )


class SqlAlchemyApiAuditLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, entry: ApiAuditLogEntry) -> None:
        self._session.add(
            ApiAuditLogRow(
                ts=entry.ts,
                caller_identity=entry.caller_identity,
                store_id=int(entry.store_id) if entry.store_id is not None else None,
                surface=entry.surface,
                route_or_tool=entry.route_or_tool,
                params_sanitized=dict(entry.params_sanitized)
                if entry.params_sanitized is not None
                else None,
                status_code=entry.status_code,
                latency_ms=entry.latency_ms,
                request_id=entry.request_id,
            )
        )
        self._session.flush()

    def list_recent(self, *, limit: int = 100) -> tuple[ApiAuditLogEntry, ...]:
        rows = self._session.scalars(
            select(ApiAuditLogRow).order_by(ApiAuditLogRow.ts.desc()).limit(limit)
        ).all()
        return tuple(_to_domain(r) for r in rows)
