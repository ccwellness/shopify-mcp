"""Contract tests for /graphql (TR-33, TR-44).

Drives Strawberry over the existing app factory + InMemory fakes, so the
same auth + audit middleware that guards /api/* is exercised here too.
GraphQL responses are JSON: `{"data": {...}, "errors": [...]}` — these
tests assert on the data tree, not raw text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from http import HTTPStatus
from typing import Any

import pytest
from flask.testing import FlaskClient

from app.domain.enums import (
    FinancialStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
    SubscriptionProvider,
)
from app.domain.models import (
    InventoryItem,
    InventoryItemId,
    InventoryLevel,
    InventoryLevelId,
    LocationId,
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    Refund,
    RefundId,
    Store,
    StoreId,
)
from app.domain.repositories import UnitOfWork

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)
LOC_A = LocationId(10)
T0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
SINCE = "2026-05-01T00:00:00+00:00"
UNTIL = "2026-05-08T00:00:00+00:00"

EXPECTED_TWO_STORES = 2
EXPECTED_TWO_ORDERS = 2


def _store(*, sid: StoreId, key: str) -> Store:
    return Store(
        id=sid,
        store_key=key,
        shop_domain=f"{key}.myshopify.com",
        display_name=key,
        plus=False,
        subscription_provider=SubscriptionProvider.UNKNOWN,
        read_only=True,
        active=True,
        timezone=None,
        currency_code="USD",
        created_at=T0,
        updated_at=T0,
    )


def _line_item(order_id: int, store_id: StoreId) -> OrderLineItem:
    return OrderLineItem(
        id=OrderLineItemId(10_000 + order_id),
        order_id=OrderId(order_id),
        store_id=store_id,
        variant_id=None,
        product_id=None,
        gid=None,
        legacy_id=None,
        title="Widget",
        sku="SKU-1",
        vendor=None,
        quantity=1,
        price=Decimal("9.99"),
        total_discount=Decimal("0.00"),
        fulfillment_status=OrderLineFulfillmentStatus.FULFILLED,
        requires_shipping=True,
        taxable=True,
    )


def _order(*, id: int, store_id: StoreId, total: Decimal = Decimal("100.00")) -> Order:  # noqa: A002
    return Order(
        id=OrderId(id),
        store_id=store_id,
        customer_id=None,
        gid=f"gid://shopify/Order/{id}",
        legacy_id=id,
        name=f"#TEST-{id}",
        order_number=id,
        email=None,
        financial_status=FinancialStatus.PAID,
        fulfillment_status=FulfillmentStatus.FULFILLED,
        currency_code="USD",
        presentment_currency_code=None,
        subtotal_price=total - Decimal("2.00"),
        total_price=total,
        total_tax=Decimal("1.00"),
        total_discounts=Decimal("0.00"),
        total_shipping=Decimal("1.00"),
        presentment_subtotal_price=None,
        presentment_total_price=None,
        processed_at=T0,
        cancelled_at=None,
        closed_at=None,
        created_at=T0,
        updated_at=T0,
        line_items=(_line_item(id, store_id),),
    )


def _refund(*, rid: int, store_id: StoreId, order_id: int, amount: Decimal) -> Refund:
    return Refund(
        id=RefundId(0),
        store_id=store_id,
        order_id=OrderId(order_id),
        gid=f"gid://shopify/Refund/{rid}",
        legacy_id=rid,
        amount=amount,
        currency_code="USD",
        note=None,
        created_at=T0,
    )


def _inv_item(*, id: int, sku: str = "SKU-A") -> InventoryItem:  # noqa: A002
    return InventoryItem(
        id=InventoryItemId(id),
        store_id=LUBELIFE,
        variant_id=None,
        gid=f"gid://shopify/InventoryItem/{id}",
        legacy_id=id,
        sku=sku,
        tracked=True,
    )


def _inv_level(*, id: int, item_id: int, available: int | None = 2) -> InventoryLevel:  # noqa: A002
    return InventoryLevel(
        id=InventoryLevelId(id),
        store_id=LUBELIFE,
        inventory_item_id=InventoryItemId(item_id),
        location_id=LOC_A,
        available=available,
        on_hand=None,
        committed=None,
        incoming=None,
        updated_at=T0,
    )


@pytest.fixture
def seed(fake_uow: UnitOfWork) -> UnitOfWork:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.stores.upsert(_store(sid=SHOPJO, key="shopjo"))
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, total=Decimal("100.00")))
        uow.orders.upsert(_order(id=2, store_id=SHOPJO, total=Decimal("200.00")))
        uow.refunds.upsert(_refund(rid=10, store_id=LUBELIFE, order_id=1, amount=Decimal("25.00")))
        uow.inventory.upsert_item(_inv_item(id=100, sku="SKU-A"))
        uow.inventory.upsert_level(_inv_level(id=1, item_id=100, available=2))
    return fake_uow


def _gql(client: FlaskClient, query: str, **variables: Any) -> dict[str, Any]:
    resp = client.post("/graphql", json={"query": query, "variables": variables})
    assert resp.status_code == HTTPStatus.OK, resp.get_data(as_text=True)
    body: dict[str, Any] = resp.get_json()
    assert "errors" not in body, body["errors"]
    return body["data"]


# ---------------------------------------------------------------------------
# Happy queries
# ---------------------------------------------------------------------------


def test_stores_query(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    data = _gql(authed_client, "{ stores { id storeKey active } }")
    keys = {s["storeKey"] for s in data["stores"]}
    assert keys == {"lubelife", "shopjo"}


def test_order_by_id_query(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    data = _gql(
        authed_client,
        "query($id: Int!) { order(id: $id) {"
        " name totalPrice currencyCode lineItems { sku quantity }"
        "} }",
        id=1,
    )
    assert data["order"]["name"] == "#TEST-1"
    assert data["order"]["totalPrice"] == "100.00"
    assert data["order"]["currencyCode"] == "USD"
    assert data["order"]["lineItems"][0]["sku"] == "SKU-1"


def test_order_by_id_missing_returns_null(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    data = _gql(authed_client, "{ order(id: 9999) { name } }")
    assert data["order"] is None


def test_orders_query_returns_page(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    data = _gql(authed_client, "{ orders { items { id name } nextCursor } }")
    assert len(data["orders"]["items"]) == EXPECTED_TWO_ORDERS
    assert data["orders"]["nextCursor"] is None


def test_orders_query_filters_by_store(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    data = _gql(
        authed_client,
        "query($sid: [Int!]) { orders(storeIds: $sid) { items { storeId name } } }",
        sid=[int(SHOPJO)],
    )
    items = data["orders"]["items"]
    assert {i["storeId"] for i in items} == {int(SHOPJO)}


def test_low_stock_query(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    data = _gql(
        authed_client,
        "{ lowStock(threshold: 10) { items { id available locationId } nextCursor } }",
    )
    items = data["lowStock"]["items"]
    assert len(items) == 1
    assert items[0]["available"] == EXPECTED_TWO_ORDERS  # 2


def test_compare_orders_query(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    data = _gql(
        authed_client,
        "query($s: DateTime!, $u: DateTime!) {"
        "  compareOrders(since: $s, until: $u) {"
        "    currencyWarning rows { storeKey paidRevenue refundsTotal netRevenue }"
        "  }"
        "}",
        s=SINCE,
        u=UNTIL,
    )
    cmp = data["compareOrders"]
    assert cmp["currencyWarning"] is False
    by_key = {r["storeKey"]: r for r in cmp["rows"]}
    assert by_key["lubelife"]["paidRevenue"] == "100.00"
    assert by_key["lubelife"]["refundsTotal"] == "25.00"
    assert by_key["lubelife"]["netRevenue"] == "75.00"
    assert by_key["shopjo"]["netRevenue"] == "200.00"


def test_compare_orders_status_counts(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    data = _gql(
        authed_client,
        "query($s: DateTime!, $u: DateTime!) {"
        "  compareOrders(since: $s, until: $u) { rows { storeKey statusCounts { status count } } }"
        "}",
        s=SINCE,
        u=UNTIL,
    )
    lube = next(r for r in data["compareOrders"]["rows"] if r["storeKey"] == "lubelife")
    assert {sc["status"]: sc["count"] for sc in lube["statusCounts"]} == {"paid": 1}


# ---------------------------------------------------------------------------
# Auth + audit
# ---------------------------------------------------------------------------


def test_graphql_requires_auth(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.post("/graphql", json={"query": "{ stores { id } }"})
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_graphql_get_loads_ide_without_auth(unauthed_client: FlaskClient) -> None:
    # GET /graphql returns the GraphiQL UI HTML — no data leaks since all
    # actual queries are POSTs that still require a bearer token.
    resp = unauthed_client.get("/graphql", headers={"Accept": "text/html"})
    assert resp.status_code == HTTPStatus.OK
    assert b"graphiql" in resp.get_data().lower() or b"<!doctype html" in resp.get_data().lower()


def test_graphql_writes_audit_row_with_graphql_surface(
    authed_client: FlaskClient, fake_uow: UnitOfWork
) -> None:
    authed_client.post("/graphql", json={"query": "{ stores { id } }"})
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert len(rows) == 1
    assert rows[0].surface == "graphql"
    assert rows[0].route_or_tool == "/graphql"
    assert rows[0].status_code == HTTPStatus.OK
