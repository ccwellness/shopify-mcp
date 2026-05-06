"""In-memory `UnitOfWork` for service-layer unit tests (TR-42).

Each `with InMemoryUnitOfWork(db) as uow: ...` block exposes the same
repository instances pointing at the same `InMemoryDatabase`, so writes
in one block are visible from the next. Commit / rollback are no-ops —
these fakes do not model SQL transactional isolation. Tests that need
to verify rollback behavior should reach for the integration suite,
which runs against real Postgres.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Self

from app.domain.repositories import UnitOfWork
from tests.fakes.repositories import (
    InMemoryAnalyticsRepository,
    InMemoryCustomerRepository,
    InMemoryDatabase,
    InMemoryInventoryRepository,
    InMemoryLocationRepository,
    InMemoryOrderRepository,
    InMemoryProductRepository,
    InMemoryStoreRepository,
    InMemorySubscriptionRepository,
    InMemorySyncStateRepository,
    InMemoryWebhookEventLogRepository,
)


class InMemoryUnitOfWork:
    """In-memory `UnitOfWork`. Constructed with a shared `InMemoryDatabase`."""

    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db
        self.stores = InMemoryStoreRepository(db)
        self.locations = InMemoryLocationRepository(db)
        self.customers = InMemoryCustomerRepository(db)
        self.orders = InMemoryOrderRepository(db)
        self.products = InMemoryProductRepository(db)
        self.inventory = InMemoryInventoryRepository(db)
        self.subscriptions = InMemorySubscriptionRepository(db)
        self.analytics = InMemoryAnalyticsRepository(db)
        self.sync_state = InMemorySyncStateRepository(db)
        self.webhook_events = InMemoryWebhookEventLogRepository(db)

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


def make_uow_factory(db: InMemoryDatabase) -> Callable[[], UnitOfWork]:
    """Return the `() -> UnitOfWork` callable services consume."""

    def factory() -> UnitOfWork:
        return InMemoryUnitOfWork(db)

    return factory
