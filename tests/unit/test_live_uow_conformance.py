"""Verify `ShopifyUnitOfWork` and its live repos satisfy the domain Protocols.

Mirrors `tests/fakes/test_protocol_conformance.py` for the live (DB-free)
implementation: the Protocols are `@runtime_checkable`, so `isinstance` does a
structural method-name check. A missing method fails here before any live tool
relies on it.
"""

from __future__ import annotations

import pytest

from app.domain.enums import SubscriptionProvider
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
from app.shopify.config import StoreConfig
from app.shopify.repositories import ShopifyUnitOfWork, build_store_index


@pytest.fixture
def uow() -> ShopifyUnitOfWork:
    configs = {
        "lubelife": StoreConfig(
            store_key="lubelife",
            shop_domain="lubelife.myshopify.com",
            client_id="",
            client_secret="",
            webhook_secret="",  # noqa: S106
            plus=False,
            subscription_provider=SubscriptionProvider.ORDERGROOVE,
            read_only=True,
            access_token="shpat_x",
        )
    }
    return ShopifyUnitOfWork(client=None, index=build_store_index(configs))


def test_shopify_uow_satisfies_protocol(uow: ShopifyUnitOfWork) -> None:
    assert isinstance(uow, UnitOfWork)


@pytest.mark.parametrize(
    ("attr", "protocol"),
    [
        ("stores", StoreRepository),
        ("locations", LocationRepository),
        ("customers", CustomerRepository),
        ("orders", OrderRepository),
        ("products", ProductRepository),
        ("inventory", InventoryRepository),
        ("refunds", RefundRepository),
        ("subscriptions", SubscriptionRepository),
        ("analytics", AnalyticsRepository),
        ("sync_state", SyncStateRepository),
        ("webhook_events", WebhookEventLogRepository),
        ("api_tokens", ApiTokenRepository),
        ("api_audit_log", ApiAuditLogRepository),
    ],
)
def test_live_repo_satisfies_protocol(uow: ShopifyUnitOfWork, attr: str, protocol: type) -> None:
    assert isinstance(getattr(uow, attr), protocol)
