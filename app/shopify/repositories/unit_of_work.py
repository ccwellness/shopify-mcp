"""ShopifyUnitOfWork — the live (database-free) UnitOfWork implementation.

Structurally identical to `SqlAlchemyUnitOfWork` and `InMemoryUnitOfWork`
(same repository attributes), so it satisfies the `app.domain.repositories`
`UnitOfWork` Protocol and drops into the container's `uow_factory` unchanged.
There is no transaction: `commit`/`rollback` are no-ops, and writes performed
by callers (e.g. `SyncService.refresh_order`) are silently discarded — live
freshness comes from reads, not persistence.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from app.shopify.client import ShopifyClient
from app.shopify.repositories.analytics import LiveAnalyticsRepository
from app.shopify.repositories.customers import LiveCustomerRepository
from app.shopify.repositories.inventory import LiveInventoryRepository
from app.shopify.repositories.locations import LiveLocationRepository
from app.shopify.repositories.null import (
    NullApiAuditLogRepository,
    NullApiTokenRepository,
    NullSyncStateRepository,
    NullWebhookEventLogRepository,
)
from app.shopify.repositories.orders import LiveOrderRepository
from app.shopify.repositories.products import LiveProductRepository
from app.shopify.repositories.refunds import LiveRefundRepository
from app.shopify.repositories.store_index import StoreIndex
from app.shopify.repositories.stores import LiveStoreRepository
from app.shopify.repositories.subscriptions import LiveSubscriptionRepository


class ShopifyUnitOfWork:
    """Live UnitOfWork backed by the Shopify Admin API + OrderGroove REST."""

    def __init__(self, client: ShopifyClient, index: StoreIndex) -> None:
        self.stores = LiveStoreRepository(index)
        self.locations = LiveLocationRepository(client, index)
        self.customers = LiveCustomerRepository(client, index)
        self.orders = LiveOrderRepository(client, index)
        self.products = LiveProductRepository(client, index)
        self.inventory = LiveInventoryRepository(client, index)
        self.refunds = LiveRefundRepository(client, index)
        self.subscriptions = LiveSubscriptionRepository(index)
        self.analytics = LiveAnalyticsRepository(client, index)
        self.sync_state = NullSyncStateRepository()
        self.webhook_events = NullWebhookEventLogRepository()
        self.api_tokens = NullApiTokenRepository()
        self.api_audit_log = NullApiAuditLogRepository()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None
