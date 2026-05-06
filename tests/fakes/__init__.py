"""In-memory repository + UoW fakes for service-layer unit tests (TR-42).

Each `InMemory*Repository` satisfies the matching Protocol from
`app.domain.repositories` structurally (no inheritance — Protocols are
runtime_checkable so tests can assert isinstance).

`InMemoryDatabase` holds the shared state. `InMemoryUnitOfWork` exposes
repositories that read/write that shared state. `make_uow_factory(db)`
returns the `() -> UnitOfWork` callable services consume.
"""

from __future__ import annotations

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
from tests.fakes.unit_of_work import InMemoryUnitOfWork, make_uow_factory

__all__ = [
    "InMemoryAnalyticsRepository",
    "InMemoryCustomerRepository",
    "InMemoryDatabase",
    "InMemoryInventoryRepository",
    "InMemoryLocationRepository",
    "InMemoryOrderRepository",
    "InMemoryProductRepository",
    "InMemoryStoreRepository",
    "InMemorySubscriptionRepository",
    "InMemorySyncStateRepository",
    "InMemoryUnitOfWork",
    "InMemoryWebhookEventLogRepository",
    "make_uow_factory",
]
