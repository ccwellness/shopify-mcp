"""Smoke tests for the internal dashboard (Phase 2 acceptance).

Dashboard routes are unauthenticated in v1 (local-only). These tests
verify each view renders without error and that key data flows from the
service layer through to the rendered HTML — but they do NOT lock the
HTML schema; that would be brittle and add no real safety.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
SINCE = "2026-05-01T00:00:00Z"
UNTIL = "2026-05-08T00:00:00Z"


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


def _inv_item(*, id: int, store_id: StoreId = LUBELIFE, sku: str = "SKU-1") -> InventoryItem:  # noqa: A002
    return InventoryItem(
        id=InventoryItemId(id),
        store_id=store_id,
        variant_id=None,
        gid=f"gid://shopify/InventoryItem/{id}",
        legacy_id=id,
        sku=sku,
        tracked=True,
    )


def _inv_level(*, id: int, item_id: int, available: int | None = 1) -> InventoryLevel:  # noqa: A002
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


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


def test_home_renders(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.get("/")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "Shopify Connector" in body
    assert "/compare" in body
    assert "/orders" in body
    assert "/inventory/low-stock" in body


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


def test_compare_renders_with_data(dashboard_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = dashboard_client.get(f"/compare?since={SINCE}&until={UNTIL}")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    # Both store keys appear in the rendered table.
    assert "lubelife" in body
    assert "shopjo" in body
    # Net revenue for lubelife = 100 - 25 = 75; for shopjo = 200.
    assert "75.00" in body
    assert "200.00" in body


def test_compare_renders_with_no_data(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.get(f"/compare?since={SINCE}&until={UNTIL}")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    # No stores seeded → either the empty-message or just an empty table.
    assert "Cross-store comparison" in body


def test_compare_shows_error_on_bad_date(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.get("/compare?since=notadate&until=" + UNTIL)
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "Invalid ISO 8601 datetime" in body


def test_compare_shows_error_on_inverted_window(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.get(f"/compare?since={UNTIL}&until={SINCE}")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "strictly before" in body


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def test_orders_renders_with_data(dashboard_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = dashboard_client.get("/orders")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "#TEST-1" in body
    assert "#TEST-2" in body


def test_orders_filter_by_store_id(dashboard_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = dashboard_client.get(f"/orders?store_id={int(SHOPJO)}")
    body = resp.get_data(as_text=True)
    assert "#TEST-2" in body
    assert "#TEST-1" not in body


def test_orders_renders_with_no_data(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.get("/orders")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "Orders" in body  # heading still renders


def test_orders_rows_partial_returns_fragment(
    dashboard_client: FlaskClient, seed: UnitOfWork
) -> None:
    # The /orders/rows endpoint is the HTMX pagination target — returns just
    # the <tr> fragment, not a full page.
    resp = dashboard_client.get("/orders/rows")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "<html" not in body.lower()
    # But it still includes the order row content.
    assert "#TEST-" in body


# ---------------------------------------------------------------------------
# Low stock
# ---------------------------------------------------------------------------


def test_low_stock_renders_with_data(dashboard_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = dashboard_client.get("/inventory/low-stock")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "Low stock" in body
    # Inventory item id 100 with available=2 should render.
    assert "100" in body


def test_low_stock_rejects_negative_threshold(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.get("/inventory/low-stock?threshold=-1")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "non-negative" in body


def test_low_stock_renders_with_no_data(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.get("/inventory/low-stock")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "Low stock" in body


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def test_analytics_renders_empty_state(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.get("/analytics?since=2026-05-09&until=2026-05-10")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "Daily KPIs" in body
    assert "No rows for this window" in body


def test_analytics_renders_with_data(dashboard_client: FlaskClient, seed: UnitOfWork) -> None:
    # Pre-seed a kpi row for lubelife May 10 so the table has content.
    from datetime import date  # noqa: PLC0415
    from decimal import Decimal  # noqa: PLC0415

    from app.domain.models import AnalyticsKpiDay  # noqa: PLC0415

    with seed as uow:
        uow.analytics.upsert_kpi_day(
            AnalyticsKpiDay(
                store_id=LUBELIFE,
                date=date(2026, 5, 10),
                sessions=1000,
                orders=25,
                units=50,
                revenue=Decimal("500.00"),
                conversion_rate=Decimal("0.0250"),
                aov=Decimal("20.00"),
                computed_at=datetime(2026, 5, 12, tzinfo=UTC),
            )
        )
    body = dashboard_client.get("/analytics?since=2026-05-10&until=2026-05-10").get_data(
        as_text=True
    )
    assert "500.00" in body
    # Conversion rendered as percentage with 2 decimals.
    assert "2.50%" in body


def test_analytics_shows_error_on_bad_date(dashboard_client: FlaskClient) -> None:
    body = dashboard_client.get("/analytics?since=notadate&until=2026-05-10").get_data(as_text=True)
    assert "Invalid date" in body


# ---------------------------------------------------------------------------
# Cross-cutting — session gate + audit-log isolation
# ---------------------------------------------------------------------------


def test_dashboard_routes_redirect_to_login_when_not_signed_in(
    unauthed_client: FlaskClient,
) -> None:
    # Without a logged-in session, every gated route 302s to /login.
    for path in (
        "/",
        "/compare",
        "/orders",
        "/inventory/low-stock",
        "/analytics",
        "/admin/tokens",
    ):
        resp = unauthed_client.get(path)
        assert resp.status_code == HTTPStatus.FOUND, f"{path} returned {resp.status_code}"
        assert "/login" in resp.headers["Location"]


def test_dashboard_does_not_write_audit_log(
    dashboard_client: FlaskClient, fake_uow: UnitOfWork
) -> None:
    # Audit middleware lives on /api/* and /graphql; dashboard pages don't
    # write audit rows even when the user is logged in.
    pre_count = len(fake_uow._db.api_audit_log)  # type: ignore[attr-defined]  # noqa: SLF001
    dashboard_client.get(f"/compare?since={SINCE}&until={UNTIL}")
    post_count = len(fake_uow._db.api_audit_log)  # type: ignore[attr-defined]  # noqa: SLF001
    # The /login POST that the dashboard_client fixture did goes through the
    # dashboard blueprint too, so it should also not have audited.
    assert pre_count == post_count
