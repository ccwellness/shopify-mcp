"""Contract tests for /api/v1/orders (TR-44).

Drives the route through Flask's test client against a Container whose
uow_factory points at the InMemory fakes — exercises the full request
path (parsing, service, serialization) without touching Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus

import pytest
from flask.testing import FlaskClient

from app.domain.enums import (
    FinancialStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
)
from app.domain.models import (
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    StoreId,
)
from app.domain.repositories import UnitOfWork

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)
T0 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

DEFAULT_LIMIT = 50  # mirrors app.services.order_query.DEFAULT_LIMIT
SEEDED_ORDER_COUNT = 3


def _line_item(*, order_id: int, line_id: int, sku: str = "SKU-1") -> OrderLineItem:
    return OrderLineItem(
        id=OrderLineItemId(line_id),
        order_id=OrderId(order_id),
        store_id=LUBELIFE,
        variant_id=None,
        product_id=None,
        gid=None,
        legacy_id=None,
        title="Widget",
        sku=sku,
        vendor=None,
        quantity=1,
        price=Decimal("9.99"),
        total_discount=Decimal("0.00"),
        fulfillment_status=OrderLineFulfillmentStatus.FULFILLED,
        requires_shipping=True,
        taxable=True,
    )


def _order(
    *,
    id: int,  # noqa: A002
    store_id: StoreId = LUBELIFE,
    processed_at: datetime = T0,
    financial_status: FinancialStatus | None = FinancialStatus.PAID,
    total_price: Decimal = Decimal("21.98"),
) -> Order:
    return Order(
        id=OrderId(id),
        store_id=store_id,
        customer_id=None,
        gid=f"gid://shopify/Order/{id}",
        legacy_id=id,
        name=f"#TEST-{id}",
        order_number=id,
        email="buyer@example.com",
        financial_status=financial_status,
        fulfillment_status=FulfillmentStatus.FULFILLED,
        currency_code="USD",
        presentment_currency_code=None,
        subtotal_price=total_price - Decimal("2.00"),
        total_price=total_price,
        total_tax=Decimal("1.00"),
        total_discounts=Decimal("0.00"),
        total_shipping=Decimal("1.00"),
        presentment_subtotal_price=None,
        presentment_total_price=None,
        processed_at=processed_at,
        cancelled_at=None,
        closed_at=None,
        created_at=processed_at,
        updated_at=processed_at,
        line_items=(_line_item(order_id=id, line_id=10_000 + id),),
    )


@pytest.fixture
def seed(fake_uow: UnitOfWork) -> UnitOfWork:
    with fake_uow as uow:
        uow.orders.upsert(_order(id=1, processed_at=T0))
        uow.orders.upsert(
            _order(
                id=2,
                processed_at=T0 + timedelta(hours=1),
                financial_status=FinancialStatus.PENDING,
            )
        )
        uow.orders.upsert(_order(id=3, store_id=SHOPJO, processed_at=T0 + timedelta(hours=2)))
    return fake_uow


# ---------------------------------------------------------------------------
# GET /api/v1/orders — happy path
# ---------------------------------------------------------------------------


def test_list_orders_empty(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/orders")
    assert resp.status_code == HTTPStatus.OK
    assert resp.get_json() == {"items": [], "next_cursor": None, "limit": DEFAULT_LIMIT}


def test_list_orders_returns_seeded(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = authed_client.get("/api/v1/orders")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_json()
    ids = [item["id"] for item in body["items"]]
    assert ids == [3, 2, 1]  # processed_at desc
    assert body["next_cursor"] is None
    assert body["limit"] == DEFAULT_LIMIT


def test_list_orders_serializes_money_as_string(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    resp = authed_client.get("/api/v1/orders")
    body = resp.get_json()
    assert body["items"][0]["total_price"] == "21.98"  # Decimal → str
    assert body["items"][0]["financial_status"] in {"paid", "pending"}  # StrEnum value


def test_list_orders_filters_by_store_id(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = authed_client.get(f"/api/v1/orders?store_id={int(LUBELIFE)}")
    body = resp.get_json()
    assert {item["store_id"] for item in body["items"]} == {int(LUBELIFE)}
    assert [item["id"] for item in body["items"]] == [2, 1]


def test_list_orders_cross_store_filter(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = authed_client.get(f"/api/v1/orders?store_id={int(LUBELIFE)}&store_id={int(SHOPJO)}")
    body = resp.get_json()
    assert len(body["items"]) == SEEDED_ORDER_COUNT


def test_list_orders_filters_by_financial_status(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    resp = authed_client.get("/api/v1/orders?financial_status=paid")
    body = resp.get_json()
    assert {item["financial_status"] for item in body["items"]} == {"paid"}


def test_list_orders_filters_by_since_until(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    # query_string urlencodes properly so the '+' in the offset survives.
    resp = authed_client.get(
        "/api/v1/orders",
        query_string={
            "since": (T0 + timedelta(minutes=30)).isoformat(),
            "until": (T0 + timedelta(hours=1, minutes=30)).isoformat(),
        },
    )
    body = resp.get_json()
    assert [item["id"] for item in body["items"]] == [2]


def test_list_orders_paginates_via_cursor(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    page1 = authed_client.get("/api/v1/orders?limit=2").get_json()
    assert [item["id"] for item in page1["items"]] == [3, 2]
    assert page1["next_cursor"] is not None

    page2 = authed_client.get(f"/api/v1/orders?limit=2&cursor={page1['next_cursor']}").get_json()
    assert [item["id"] for item in page2["items"]] == [1]
    assert page2["next_cursor"] is None


# ---------------------------------------------------------------------------
# GET /api/v1/orders — bad input → 400
# ---------------------------------------------------------------------------


def test_list_orders_rejects_bad_financial_status(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/orders?financial_status=banana")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "financial_status" in resp.get_json()["error"]


def test_list_orders_rejects_non_integer_limit(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/orders?limit=abc")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "limit" in resp.get_json()["error"]


def test_list_orders_rejects_bad_since(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/orders?since=not-a-date")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "since" in resp.get_json()["error"]


def test_list_orders_rejects_non_integer_store_id(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/orders?store_id=not-an-int")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_list_orders_rejects_bad_min_total(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/orders?min_total=not-a-decimal")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# GET /api/v1/orders/<id>
# ---------------------------------------------------------------------------


def test_get_order_returns_full_aggregate(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = authed_client.get("/api/v1/orders/1")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_json()
    assert body["id"] == 1
    assert len(body["line_items"]) == 1
    assert body["line_items"][0]["sku"] == "SKU-1"


def test_get_order_404_for_missing(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/orders/9999")
    assert resp.status_code == HTTPStatus.NOT_FOUND
    assert "not found" in resp.get_json()["error"]
