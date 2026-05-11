"""Unit tests for `SyncService.sync_refunds`.

Drives the service against the InMemory persistence layer and a stub
Shopify client that records every `query()` call. No HTTP, no Postgres.

Covered behavior:
- Walks orders with financial_status in {refunded, partially_refunded}
- One GraphQL call per refunded order; payload's refunds are upserted
- Empty `refunds` array means no upsert + no commit
- Returns the total upsert count and records sync_state
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import (
    FinancialStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
    SubscriptionProvider,
    SyncResource,
)
from app.domain.models import (
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    Store,
    StoreId,
)
from app.domain.repositories import UnitOfWork
from app.services.sync import SyncService
from app.shopify.config import StoreConfig

STORE_KEY = "lubelife"
T0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

# 4 refund records expected: 2 from order 1 + 1 from order 2 + 0 from order 3 + 1 from order 4.
EXPECTED_REFUND_COUNT = 4
EXPECTED_REFUND_TOTAL = Decimal("20.00")  # 4 refunds × $5.00 each


def _store_config() -> StoreConfig:
    return StoreConfig(
        store_key=STORE_KEY,
        shop_domain="lubelife.myshopify.com",
        client_id="cid",
        client_secret="csecret",  # noqa: S106 — test fixture, not a real secret
        webhook_secret="wsecret",  # noqa: S106 — test fixture, not a real secret
        plus=False,
        subscription_provider=SubscriptionProvider.UNKNOWN,
        read_only=True,
    )


def _seed_store(uow: UnitOfWork) -> StoreId:
    with uow as u:
        u.stores.upsert(
            Store(
                id=StoreId(0),
                store_key=STORE_KEY,
                shop_domain="lubelife.myshopify.com",
                display_name="lubelife",
                plus=False,
                subscription_provider=SubscriptionProvider.UNKNOWN,
                read_only=True,
                active=True,
                timezone=None,
                currency_code=None,
                created_at=T0,
                updated_at=T0,
            )
        )
        u.commit()
        loaded = u.stores.get_by_key(STORE_KEY)
    assert loaded is not None
    return loaded.id


def _line_item(order_id: int, store_id: StoreId, *, item_id: int) -> OrderLineItem:
    return OrderLineItem(
        id=OrderLineItemId(item_id),
        order_id=OrderId(order_id),
        store_id=store_id,
        variant_id=None,
        product_id=None,
        gid=None,
        legacy_id=None,
        title="W",
        sku="S",
        vendor=None,
        quantity=1,
        price=Decimal("1.00"),
        total_discount=Decimal("0.00"),
        fulfillment_status=OrderLineFulfillmentStatus.FULFILLED,
        requires_shipping=True,
        taxable=True,
    )


def _order(
    *,
    id: int,  # noqa: A002
    store_id: StoreId,
    financial_status: FinancialStatus,
) -> Order:
    return Order(
        id=OrderId(id),
        store_id=store_id,
        customer_id=None,
        gid=f"gid://shopify/Order/{id}",
        legacy_id=id,
        name=f"#R-{id}",
        order_number=id,
        email=None,
        financial_status=financial_status,
        fulfillment_status=FulfillmentStatus.FULFILLED,
        currency_code="USD",
        presentment_currency_code=None,
        subtotal_price=Decimal("1.00"),
        total_price=Decimal("1.00"),
        total_tax=Decimal("0.00"),
        total_discounts=Decimal("0.00"),
        total_shipping=Decimal("0.00"),
        presentment_subtotal_price=None,
        presentment_total_price=None,
        processed_at=T0,
        cancelled_at=None,
        closed_at=None,
        created_at=T0,
        updated_at=T0,
        line_items=(_line_item(order_id=id, store_id=store_id, item_id=10_000 + id),),
    )


def _refund_node(refund_id: str, amount: str = "5.00") -> dict[str, Any]:
    return {
        "id": f"gid://shopify/Refund/{refund_id}",
        "legacyResourceId": refund_id,
        "note": None,
        "createdAt": "2026-05-01T10:30:00+00:00",
        "totalRefundedSet": {"shopMoney": {"amount": amount, "currencyCode": "USD"}},
    }


class _StubClient:
    """Records every `query()` call and returns canned refund payloads keyed by order gid."""

    def __init__(self, refunds_by_order_gid: dict[str, list[dict[str, Any]]]) -> None:
        self._refunds = refunds_by_order_gid
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def query(
        self,
        store_key: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_mutation: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((store_key, variables))
        order_gid = (variables or {}).get("id", "")
        return {
            "order": {
                "id": order_gid,
                "refunds": list(self._refunds.get(order_gid, [])),
            }
        }


@pytest.fixture
def service(fake_uow_factory: Callable[[], UnitOfWork]) -> tuple[SyncService, _StubClient]:
    # Seed STORE_KEY's row before constructing the service so the stub
    # client doesn't need to participate in store resolution.
    _seed_store(fake_uow_factory())
    client = _StubClient(
        refunds_by_order_gid={
            "gid://shopify/Order/1": [_refund_node("101"), _refund_node("102")],
            "gid://shopify/Order/2": [_refund_node("201")],
            "gid://shopify/Order/3": [],  # refunded order with no refund records
            "gid://shopify/Order/4": [_refund_node("401")],
        }
    )
    svc = SyncService(
        uow_factory=fake_uow_factory,
        shopify_client=client,  # type: ignore[arg-type]
        bulk_client=object(),  # type: ignore[arg-type] — sync_refunds doesn't touch bulk
        store_configs={STORE_KEY: _store_config()},
    )
    return svc, client


def _seed_orders(uow_factory: Callable[[], UnitOfWork]) -> StoreId:
    with uow_factory() as u:
        store = u.stores.get_by_key(STORE_KEY)
    assert store is not None
    store_id = store.id
    with uow_factory() as u:
        # Refund-eligible orders.
        u.orders.upsert(_order(id=1, store_id=store_id, financial_status=FinancialStatus.REFUNDED))
        u.orders.upsert(
            _order(
                id=2,
                store_id=store_id,
                financial_status=FinancialStatus.PARTIALLY_REFUNDED,
            )
        )
        u.orders.upsert(_order(id=3, store_id=store_id, financial_status=FinancialStatus.REFUNDED))
        u.orders.upsert(
            _order(
                id=4,
                store_id=store_id,
                financial_status=FinancialStatus.PARTIALLY_REFUNDED,
            )
        )
        # Not refund-eligible — should be ignored.
        u.orders.upsert(_order(id=5, store_id=store_id, financial_status=FinancialStatus.PAID))
        u.commit()
    return store_id


def test_sync_refunds_walks_only_refunded_orders_and_upserts(
    service: tuple[SyncService, _StubClient],
    fake_uow_factory: Callable[[], UnitOfWork],
) -> None:
    svc, client = service
    store_id = _seed_orders(fake_uow_factory)

    result = svc.sync_refunds(STORE_KEY)

    # 4 refund records inserted across 3 orders (one order had an empty refund list).
    assert result.upserted == EXPECTED_REFUND_COUNT
    assert result.store_key == STORE_KEY
    assert result.resource == SyncResource.REFUNDS

    # One query per refund-eligible order — the PAID order is skipped.
    queried_order_gids = {(vars_ or {}).get("id") for _, vars_ in client.calls}
    assert queried_order_gids == {
        "gid://shopify/Order/1",
        "gid://shopify/Order/2",
        "gid://shopify/Order/3",
        "gid://shopify/Order/4",
    }
    assert "gid://shopify/Order/5" not in queried_order_gids

    # Refunds landed in the DB with correct linkage.
    with fake_uow_factory() as u:
        for_o1 = u.refunds.list_for_order(OrderId(1))
        for_o3 = u.refunds.list_for_order(OrderId(3))
    assert {r.gid for r in for_o1} == {
        "gid://shopify/Refund/101",
        "gid://shopify/Refund/102",
    }
    # Empty refunds array → no refund row written for order 3.
    assert for_o3 == ()

    # sync_state row recorded.
    with fake_uow_factory() as u:
        row = u.sync_state.get(store_id, SyncResource.REFUNDS)
    assert row is not None
    assert row.last_completed_at is not None


def test_sync_refunds_with_no_eligible_orders_makes_no_queries(
    service: tuple[SyncService, _StubClient],
    fake_uow_factory: Callable[[], UnitOfWork],
) -> None:
    svc, client = service
    # Don't seed any refund-eligible orders.
    result = svc.sync_refunds(STORE_KEY)
    assert result.upserted == 0
    assert client.calls == []


def test_sync_refunds_upsert_is_idempotent_on_rerun(
    service: tuple[SyncService, _StubClient],
    fake_uow_factory: Callable[[], UnitOfWork],
) -> None:
    svc, _ = service
    _seed_orders(fake_uow_factory)

    first = svc.sync_refunds(STORE_KEY)
    second = svc.sync_refunds(STORE_KEY)

    # Same payload on the second run — counts equal, but no duplicate rows.
    assert first.upserted == second.upserted == EXPECTED_REFUND_COUNT
    with fake_uow_factory() as u:
        total = u.refunds.sum_in_window(
            # All seeded refunds are at 2026-05-01T10:30Z → bracket generously.
            store_id=u.stores.get_by_key(STORE_KEY).id,  # type: ignore[union-attr]
            since=T0 - timedelta(days=30),
            until=T0 + timedelta(days=30),
        )
    assert total == EXPECTED_REFUND_TOTAL
