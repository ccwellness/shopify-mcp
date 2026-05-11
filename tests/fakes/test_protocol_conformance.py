"""Verify every InMemory fake structurally satisfies its Protocol.

The Protocols in `app.domain.repositories` are `@runtime_checkable`, so
`isinstance(fake, Protocol)` does a method-name structural check at
runtime. If a fake forgets a method, this test fails — which is what we
want before any service test starts using the fake.
"""

from __future__ import annotations

import pytest

from app.domain.repositories import (
    AnalyticsRepository,
    ApiAuditLogRepository,
    ApiTokenRepository,
    CustomerRepository,
    InventoryRepository,
    LocationRepository,
    OrderRepository,
    ProductRepository,
    RefundRepository,
    StoreRepository,
    SubscriptionRepository,
    SyncStateRepository,
    UnitOfWork,
    WebhookEventLogRepository,
)
from tests.fakes import InMemoryDatabase, InMemoryUnitOfWork


@pytest.fixture
def db() -> InMemoryDatabase:
    return InMemoryDatabase()


@pytest.fixture
def uow(db: InMemoryDatabase) -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(db)


def test_unit_of_work_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow, UnitOfWork)


def test_store_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.stores, StoreRepository)


def test_location_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.locations, LocationRepository)


def test_customer_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.customers, CustomerRepository)


def test_order_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.orders, OrderRepository)


def test_product_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.products, ProductRepository)


def test_inventory_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.inventory, InventoryRepository)


def test_refund_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.refunds, RefundRepository)


def test_subscription_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.subscriptions, SubscriptionRepository)


def test_analytics_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.analytics, AnalyticsRepository)


def test_sync_state_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.sync_state, SyncStateRepository)


def test_webhook_event_log_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.webhook_events, WebhookEventLogRepository)


def test_api_token_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.api_tokens, ApiTokenRepository)


def test_api_audit_log_repo_satisfies_protocol(uow: InMemoryUnitOfWork) -> None:
    assert isinstance(uow.api_audit_log, ApiAuditLogRepository)
