"""WebhookDispatcherService — picks up webhook_events_log rows and routes by topic.

Lives at L4 (services). Reads a row from `uow.webhook_events`, decompresses
the payload, normalizes it via the appropriate normalizer in
`app.shopify.normalizers`, then orchestrates the dependent upserts (e.g.
customer-before-order) inside the same UoW so the whole delivery either
lands or rolls back.

Failure handling: on any exception during dispatch, we rollback the work
UoW and open a fresh UoW to mark the row as `failed`. Two transactions
on purpose — we never want a "rollback erased my failure flag" race.

Currently implements: orders/create, orders/updated, orders/paid,
orders/cancelled, orders/fulfilled. All other subscribed topics are
marked failed with "not yet implemented" so the queue stays clean.
"""

from __future__ import annotations

import dataclasses
import json
import traceback
from collections.abc import Callable
from typing import Any

from app.domain.models import StoreId
from app.domain.repositories import UnitOfWork
from app.shopify.normalizers.orders import normalize_order_webhook

ORDER_TOPICS = frozenset(
    {
        "orders/create",
        "orders/updated",
        "orders/paid",
        "orders/cancelled",
        "orders/fulfilled",
    }
)


def dispatch_webhook_event(
    event_id: int,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Process one webhook delivery to completion (or recorded failure)."""
    try:
        with uow_factory() as uow:
            info = uow.webhook_events.get_for_processing(event_id)
            if info is None:
                # Row missing or already processed — nothing to do.
                return
            store_id, topic, raw_body = info
            payload = json.loads(raw_body.decode("utf-8"))

            if topic in ORDER_TOPICS:
                _handle_order(uow, store_id, payload)
            elif topic == "app/uninstalled":
                _handle_app_uninstalled(uow, store_id)
            else:
                uow.webhook_events.mark_failed(event_id, f"topic {topic!r} not yet implemented")
                uow.commit()
                return

            uow.webhook_events.mark_processed(event_id)
            uow.commit()
    except Exception as exc:
        # Open a fresh UoW so the failure flag survives even if the work UoW
        # rolled back. Truncated traceback so a stack can't fill the column.
        tb = traceback.format_exception_only(type(exc), exc)[-1].strip()
        try:
            with uow_factory() as uow_fail:
                uow_fail.webhook_events.mark_failed(event_id, tb)
                uow_fail.commit()
        except Exception as fail_exc:  # noqa: BLE001
            # If even the failure-marking write fails, surface the original
            # by re-raising below; this branch only swallows the secondary.
            _ = fail_exc
        raise


# ---------------------------------------------------------------------------
# Per-topic handlers
# ---------------------------------------------------------------------------


def _handle_order(uow: UnitOfWork, store_id: StoreId, payload: dict[str, Any]) -> None:
    normalized = normalize_order_webhook(store_id, payload)

    customer_id = None
    if normalized.customer is not None:
        uow.customers.upsert(normalized.customer)
        # Re-read to pick up the assigned DB id.
        loaded = uow.customers.get_by_gid(store_id, normalized.customer.gid)
        if loaded is not None:
            customer_id = loaded.id

    order_with_customer = dataclasses.replace(normalized.order, customer_id=customer_id)
    uow.orders.upsert(order_with_customer)


def _handle_app_uninstalled(uow: UnitOfWork, store_id: StoreId) -> None:
    """Defensive: when Shopify uninstalls our app, mark the store inactive
    so subsequent sync attempts won't blast tokens at a dead app."""
    store = uow.stores.get(store_id)
    if store is None:
        return
    deactivated = dataclasses.replace(store, active=False)
    uow.stores.upsert(deactivated)
