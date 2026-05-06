"""WebhookIngestService — the only thing the receiver view calls.

Resolves `store_key` (from URL) to a `store_id` via the StoreRepository.
First-webhook-creates-store: if the StoreRow doesn't exist yet, build one
from the in-memory `StoreConfig` and upsert it, so a fresh dev DB doesn't
need a separate `flask sync init-stores` step.

After persisting the `webhook_events_log` row, enqueues a dispatch job
through the configured `JobQueue`:

- `InlineJobQueue` runs the dispatch synchronously on the same thread —
  fine for tests / dev without Redis.
- `RqJobQueue` queues for an `rq worker` process to pick up.

The service writes the log row inside its own UoW and commits immediately
(TR-12 — fast 200 OK). The dispatcher opens a fresh UoW of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.domain.repositories import UnitOfWork
from app.jobs.queue import JobQueue
from app.services._store_resolver import ensure_store
from app.services.webhook_dispatcher import dispatch_webhook_event
from app.shopify.config import StoreConfig


class UnknownStoreError(Exception):
    """Raised when an inbound webhook references a store_key the connector doesn't know about."""


def _dispatch_with_factory(event_id: int, uow_factory: Callable[[], UnitOfWork]) -> None:
    """Module-level adapter so `enqueue` doesn't have to capture self."""
    dispatch_webhook_event(event_id, uow_factory)


class WebhookIngestService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        store_configs: dict[str, StoreConfig],
        job_queue: JobQueue,
    ) -> None:
        self._uow_factory = uow_factory
        self._configs = store_configs
        self._job_queue = job_queue

    def record(
        self,
        *,
        store_key: str,
        topic: str,
        shopify_webhook_id: str | None,
        raw_body: bytes,
        hmac_valid: bool,
    ) -> int:
        """Persist one webhook delivery and enqueue dispatch. Returns the new event id."""
        cfg = self._configs.get(store_key)
        if cfg is None:
            raise UnknownStoreError(store_key)

        with self._uow_factory() as uow:
            store_id = ensure_store(uow, cfg)
            event_id = uow.webhook_events.record(
                store_id=store_id,
                topic=topic,
                shopify_webhook_id=shopify_webhook_id,
                received_at=datetime.now(tz=UTC),
                hmac_valid=hmac_valid,
                raw_body=raw_body,
            )
            uow.commit()

        # Dispatch runs on a fresh UoW; we pass our factory so it can build one.
        self._job_queue.enqueue(_dispatch_with_factory, event_id, self._uow_factory)
        return event_id
