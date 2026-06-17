"""No-op repositories for live mode.

These cover the persistence-only concerns that have no live-API meaning:
the audit log (writes dropped), bearer tokens (no DB token store), sync state,
and the webhook event log. Read methods return empty/None so callers degrade
gracefully; writes are accepted and discarded, mirroring the in-memory fake's
no-op `commit`.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.enums import SyncResource
from app.domain.models import (
    ApiAuditLogEntry,
    ApiToken,
    ApiTokenId,
    StoreId,
    SyncStateRow,
)


class NullApiAuditLogRepository:
    def record(self, entry: ApiAuditLogEntry) -> None:  # noqa: ARG002 — dropped in live mode
        return None

    def list_recent(self, *, limit: int = 100) -> tuple[ApiAuditLogEntry, ...]:  # noqa: ARG002
        return ()


class NullApiTokenRepository:
    """Live mode has no DB token store. HTTP auth uses MCP_STATIC_TOKEN instead."""

    def get_by_hash(self, token_hash: str) -> ApiToken | None:  # noqa: ARG002
        return None

    def list_active(self) -> tuple[ApiToken, ...]:
        return ()

    def upsert(self, token: ApiToken) -> ApiTokenId:
        raise NotImplementedError("token persistence is unavailable in live mode")

    def touch_last_used(self, token_id: ApiTokenId, when: datetime) -> None:
        raise NotImplementedError("token persistence is unavailable in live mode")

    def revoke(self, token_id: ApiTokenId, when: datetime) -> None:
        raise NotImplementedError("token persistence is unavailable in live mode")


class NullSyncStateRepository:
    def get(self, store_id: StoreId, resource: SyncResource) -> SyncStateRow | None:  # noqa: ARG002
        return None

    def list_for_store(self, store_id: StoreId) -> tuple[SyncStateRow, ...]:  # noqa: ARG002
        return ()

    def upsert(self, row: SyncStateRow) -> None:  # noqa: ARG002 — no sync state to persist
        return None


class NullWebhookEventLogRepository:
    def record(  # noqa: PLR0913
        self,
        *,
        store_id: StoreId,
        topic: str,
        shopify_webhook_id: str | None,
        received_at: datetime,
        hmac_valid: bool,
        raw_body: bytes,
    ) -> int:
        raise NotImplementedError("webhook ingest is unavailable in live mode")

    def get_for_processing(self, event_id: int) -> tuple[StoreId, str, bytes] | None:  # noqa: ARG002
        return None

    def mark_processed(self, event_id: int) -> None:  # noqa: ARG002
        return None

    def mark_failed(self, event_id: int, error: str) -> None:  # noqa: ARG002
        return None
