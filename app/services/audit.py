"""AuditService — append a row to api_audit_log (TR-6).

Sanitization is intentionally minimal in v1 — we drop nothing, since the
internal API surface accepts no PII fields today. The pre-write filter
exists as a hook for future PII-redaction work (TR-47).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.domain.models import ApiAuditLogEntry, ApiAuditLogId, StoreId
from app.domain.repositories import UnitOfWork

# Keys whose values are never persisted, even if a future endpoint accepts them.
_ALWAYS_REDACT = frozenset({"password", "token", "secret", "authorization", "api_key"})


def _sanitize(params: dict[str, object] | None) -> dict[str, object] | None:
    if params is None:
        return None
    return {k: ("[REDACTED]" if k.lower() in _ALWAYS_REDACT else v) for k, v in params.items()}


class AuditService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def record(  # noqa: PLR0913 — kwargs-only API; one-row append needs every field
        self,
        *,
        caller_identity: str,
        store_id: StoreId | None,
        surface: str,
        route_or_tool: str,
        params: dict[str, object] | None,
        status_code: int | None,
        latency_ms: int | None,
        request_id: str | None,
        ts: datetime | None = None,
    ) -> None:
        entry = ApiAuditLogEntry(
            id=ApiAuditLogId(0),  # placeholder; repo assigns the real id
            ts=ts or datetime.now(tz=UTC),
            caller_identity=caller_identity,
            store_id=store_id,
            surface=surface,
            route_or_tool=route_or_tool,
            params_sanitized=_sanitize(params),
            status_code=status_code,
            latency_ms=latency_ms,
            request_id=request_id,
        )
        with self._uow_factory() as uow:
            uow.api_audit_log.record(entry)
            uow.commit()
