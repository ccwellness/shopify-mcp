"""Contract tests for /api/v1/compare/orders (TR-32, TR-44)."""

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
    SubscriptionProvider,
)
from app.domain.models import (
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

T0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
# Use `Z` suffix instead of `+00:00` — the `+` gets URL-decoded as a space
# inside query strings, breaking the ISO parser.
SINCE = "2026-05-01T00:00:00Z"
UNTIL = "2026-05-08T00:00:00Z"

EXPECTED_TWO_ROWS = 2


def _store(*, sid: StoreId, key: str, currency: str = "USD") -> Store:
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
        currency_code=currency,
        created_at=T0,
        updated_at=T0,
    )


def _line_item(order_id: int, store_id: StoreId, *, quantity: int = 1) -> OrderLineItem:
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
        quantity=quantity,
        price=Decimal("9.99"),
        total_discount=Decimal("0.00"),
        fulfillment_status=OrderLineFulfillmentStatus.FULFILLED,
        requires_shipping=True,
        taxable=True,
    )


def _order(  # noqa: PLR0913 — test builder
    *,
    id: int,  # noqa: A002
    store_id: StoreId,
    total_price: Decimal = Decimal("100.00"),
    currency_code: str = "USD",
    processed_at: datetime = T0,
) -> Order:
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
        currency_code=currency_code,
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


@pytest.fixture
def seed(fake_uow: UnitOfWork) -> UnitOfWork:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.stores.upsert(_store(sid=SHOPJO, key="shopjo"))
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, total_price=Decimal("100.00")))
        uow.orders.upsert(_order(id=2, store_id=SHOPJO, total_price=Decimal("200.00")))
        uow.refunds.upsert(_refund(rid=10, store_id=LUBELIFE, order_id=1, amount=Decimal("25.00")))
    return fake_uow


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_compare_orders_returns_per_store_rows(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    resp = authed_client.get(f"/api/v1/compare/orders?since={SINCE}&until={UNTIL}")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_json()

    # Datetime round-trips through Python's isoformat — `Z` becomes `+00:00` on output.
    assert body["since"] == "2026-05-01T00:00:00+00:00"
    assert body["until"] == "2026-05-08T00:00:00+00:00"
    assert body["currency_warning"] is False
    assert len(body["rows"]) == EXPECTED_TWO_ROWS

    by_key = {r["store_key"]: r for r in body["rows"]}
    assert by_key["lubelife"]["paid_revenue"] == "100.00"
    assert by_key["lubelife"]["refunds_total"] == "25.00"
    assert by_key["lubelife"]["net_revenue"] == "75.00"
    assert by_key["shopjo"]["paid_revenue"] == "200.00"
    assert by_key["shopjo"]["refunds_total"] == "0"
    assert by_key["shopjo"]["net_revenue"] == "200.00"


def test_compare_orders_serializes_status_counts_as_string_keys(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    body = authed_client.get(f"/api/v1/compare/orders?since={SINCE}&until={UNTIL}").get_json()
    lubelife = next(r for r in body["rows"] if r["store_key"] == "lubelife")
    # Enum keys must serialize to their string value, not "FinancialStatus.PAID".
    assert lubelife["status_counts"] == {"paid": 1}


def test_compare_orders_empty_window_returns_zeroed_rows(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    # Window starts after every seeded order — every row reports zeros.
    # Strip the `+00:00` offset that isoformat emits — `+` gets URL-decoded as space.
    future = (T0 + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    later = (T0 + timedelta(days=37)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = authed_client.get(f"/api/v1/compare/orders?since={future}&until={later}").get_json()
    for row in body["rows"]:
        assert row["order_count"] == 0
        assert row["paid_revenue"] == "0.00"
        assert row["net_revenue"] == "0.00"


def test_compare_orders_filters_by_store_id(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = authed_client.get(
        f"/api/v1/compare/orders?since={SINCE}&until={UNTIL}&store_id={int(SHOPJO)}"
    )
    body = resp.get_json()
    assert {r["store_key"] for r in body["rows"]} == {"shopjo"}


def test_compare_orders_flags_currency_warning(
    authed_client: FlaskClient, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife", currency="USD"))
        uow.stores.upsert(_store(sid=SHOPJO, key="shopjo", currency="CAD"))
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, currency_code="USD"))
        uow.orders.upsert(_order(id=2, store_id=SHOPJO, currency_code="CAD"))
    body = authed_client.get(f"/api/v1/compare/orders?since={SINCE}&until={UNTIL}").get_json()
    assert body["currency_warning"] is True


# ---------------------------------------------------------------------------
# Bad input → 400
# ---------------------------------------------------------------------------


def test_compare_orders_missing_since_returns_400(authed_client: FlaskClient) -> None:
    resp = authed_client.get(f"/api/v1/compare/orders?until={UNTIL}")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "since" in resp.get_json()["error"]


def test_compare_orders_missing_until_returns_400(authed_client: FlaskClient) -> None:
    resp = authed_client.get(f"/api/v1/compare/orders?since={SINCE}")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "until" in resp.get_json()["error"]


def test_compare_orders_malformed_datetime_returns_400(authed_client: FlaskClient) -> None:
    resp = authed_client.get(f"/api/v1/compare/orders?since=notadate&until={UNTIL}")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "ISO 8601" in resp.get_json()["error"]


def test_compare_orders_inverted_window_returns_400(authed_client: FlaskClient) -> None:
    # The service raises ValueError → 400.
    resp = authed_client.get(f"/api/v1/compare/orders?since={UNTIL}&until={SINCE}")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "strictly before" in resp.get_json()["error"]


def test_compare_orders_non_integer_store_id_returns_400(authed_client: FlaskClient) -> None:
    resp = authed_client.get(f"/api/v1/compare/orders?since={SINCE}&until={UNTIL}&store_id=abc")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# Auth + audit (smoke)
# ---------------------------------------------------------------------------


def test_compare_orders_requires_auth(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.get(f"/api/v1/compare/orders?since={SINCE}&until={UNTIL}")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_compare_orders_writes_audit_row(authed_client: FlaskClient, fake_uow: UnitOfWork) -> None:
    authed_client.get(f"/api/v1/compare/orders?since={SINCE}&until={UNTIL}")
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert len(rows) == 1
    assert rows[0].route_or_tool == "/api/v1/compare/orders"
    assert rows[0].status_code == HTTPStatus.OK
