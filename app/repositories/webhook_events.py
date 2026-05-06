"""SqlAlchemyWebhookEventLogRepository — operational log for webhook deliveries.

Compresses the raw body with gzip before persistence. The raw body is the
sole source of truth for the dispatcher (which decompresses, JSON-parses,
and routes by topic) — so we lose nothing by storing it verbatim instead
of carving it into typed columns.
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.orm.webhook_event import WebhookEventRow
from app.domain.enums import WebhookProcessingStatus
from app.domain.models import StoreId


class SqlAlchemyWebhookEventLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(  # noqa: PLR0913 — kwargs-only by Protocol contract, not refactorable
        self,
        *,
        store_id: StoreId,
        topic: str,
        shopify_webhook_id: str | None,
        received_at: datetime,
        hmac_valid: bool,
        raw_body: bytes,
    ) -> int:
        compressed = gzip.compress(raw_body, compresslevel=6)
        row = WebhookEventRow(
            store_id=int(store_id),
            topic=topic,
            shopify_webhook_id=shopify_webhook_id,
            received_at=received_at,
            hmac_valid=hmac_valid,
            payload_compressed=compressed,
            payload_size=len(raw_body),
            processing_status=WebhookProcessingStatus.RECEIVED.value,
        )
        self._session.add(row)
        self._session.flush()
        return row.id

    def get_for_processing(self, event_id: int) -> tuple[StoreId, str, bytes] | None:
        row = self._session.get(WebhookEventRow, event_id)
        if row is None or row.processing_status == WebhookProcessingStatus.PROCESSED.value:
            return None
        raw = gzip.decompress(row.payload_compressed)
        return (StoreId(row.store_id), row.topic, raw)

    def mark_processed(self, event_id: int) -> None:
        row = self._session.get(WebhookEventRow, event_id)
        if row is None:
            return
        row.processing_status = WebhookProcessingStatus.PROCESSED.value
        row.processed_at = datetime.now(tz=UTC)
        row.error = None
        self._session.flush()

    def mark_failed(self, event_id: int, error: str) -> None:
        row = self._session.get(WebhookEventRow, event_id)
        if row is None:
            return
        row.processing_status = WebhookProcessingStatus.FAILED.value
        row.processed_at = datetime.now(tz=UTC)
        row.error = error[:1000]  # truncate so a stack trace can't fill a TEXT column
        self._session.flush()

    # Re-export to silence "imported but unused" — `select` is here to keep
    # bulk introspection patterns ergonomic in future expansions.
    _ = select
