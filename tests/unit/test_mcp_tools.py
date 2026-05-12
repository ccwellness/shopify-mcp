"""Unit tests for the six MCP tools (TR-34 subset).

Drives the tools through `mcp.call_tool` so we exercise the same
Pydantic-bound argument coercion the LLM will hit. The container is
overridden to point at the in-memory persistence layer + a test-mode
container that skips Shopify credentials.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from dependency_injector import providers

# Tool registration happens on import:
import mcp_server.tools  # noqa: F401, E402
from app.container import Container
from app.domain.enums import (
    AnalyticsSource,
    FinancialStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
    SubscriptionProvider,
)
from app.domain.models import (
    AnalyticsKpiDay,
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
    SessionsDay,
    Store,
    StoreId,
)
from app.domain.repositories import UnitOfWork
from mcp_server.server import mcp, set_container_for_tests

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)
T0 = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
DAY = date(2026, 5, 10)


@pytest.fixture
def mcp_container(fake_uow_factory: Callable[[], UnitOfWork]) -> Iterator[Container]:
    """Build a Container pointed at the in-memory fakes, install it on the
    MCP module's singleton, and tear it down after."""
    c = Container()
    c.uow_factory.override(providers.Object(fake_uow_factory))
    c.store_configs.override(providers.Object({}))
    set_container_for_tests(c)
    try:
        yield c
    finally:
        set_container_for_tests(None)
        c.unwire()
        c.reset_override()


def _call(name: str, **args: object) -> Any:
    """Helper: invoke a tool and return its structured_content."""
    result = asyncio.run(mcp.call_tool(name, args))
    return result.structured_content


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


def _line_item(order_id: int, store_id: StoreId, *, sku: str = "SKU-1") -> OrderLineItem:
    return OrderLineItem(
        id=OrderLineItemId(10_000 + order_id),
        order_id=OrderId(order_id),
        store_id=store_id,
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


def _inv_item(*, id: int) -> InventoryItem:  # noqa: A002
    return InventoryItem(
        id=InventoryItemId(id),
        store_id=LUBELIFE,
        variant_id=None,
        gid=f"gid://shopify/InventoryItem/{id}",
        legacy_id=id,
        sku="SKU-A",
        tracked=True,
    )


def _inv_level(*, id: int, item_id: int, available: int) -> InventoryLevel:  # noqa: A002
    return InventoryLevel(
        id=InventoryLevelId(id),
        store_id=LUBELIFE,
        inventory_item_id=InventoryItemId(item_id),
        location_id=LocationId(10),
        available=available,
        on_hand=None,
        committed=None,
        incoming=None,
        updated_at=T0,
    )


@pytest.fixture
def seed_full(mcp_container: Container, fake_uow: UnitOfWork) -> UnitOfWork:
    """Seed stores + orders + refunds + inventory + a kpi row."""
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.stores.upsert(_store(sid=SHOPJO, key="shopjo"))
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, total=Decimal("100.00")))
        uow.orders.upsert(_order(id=2, store_id=SHOPJO, total=Decimal("200.00")))
        uow.refunds.upsert(_refund(rid=10, store_id=LUBELIFE, order_id=1, amount=Decimal("25.00")))
        uow.inventory.upsert_item(_inv_item(id=100))
        uow.inventory.upsert_level(_inv_level(id=1, item_id=100, available=3))
        uow.analytics.upsert_sessions_day(
            SessionsDay(
                store_id=LUBELIFE,
                date=DAY,
                sessions=1000,
                orders=25,
                total_sales=Decimal("500.00"),
                units_sold=None,
                source=AnalyticsSource.SHOPIFYQL,
                pulled_at=T0,
            )
        )
        uow.analytics.upsert_kpi_day(
            AnalyticsKpiDay(
                store_id=LUBELIFE,
                date=DAY,
                sessions=1000,
                orders=25,
                units=50,
                revenue=Decimal("500.00"),
                conversion_rate=Decimal("0.0250"),
                aov=Decimal("20.00"),
                computed_at=T0,
            )
        )
    return fake_uow


# ---------------------------------------------------------------------------
# list_stores
# ---------------------------------------------------------------------------


def test_list_stores_returns_active_rows(seed_full: UnitOfWork) -> None:
    out = _call("list_stores")
    keys = {s["store_key"] for s in out["items"]}
    assert keys == {"lubelife", "shopjo"}


# ---------------------------------------------------------------------------
# list_orders / get_order
# ---------------------------------------------------------------------------


def test_list_orders_returns_cross_store(seed_full: UnitOfWork) -> None:
    out = _call("list_orders")
    expected_rows = 2
    assert len(out["items"]) == expected_rows
    assert out["next_cursor"] is None


def test_list_orders_filters_by_store_id(seed_full: UnitOfWork) -> None:
    out = _call("list_orders", store_id=[int(SHOPJO)])
    assert {o["store_id"] for o in out["items"]} == {int(SHOPJO)}


def test_get_order_by_numeric_id(seed_full: UnitOfWork) -> None:
    out = _call("get_order", order_id=1)
    assert out["order"]["name"] == "#TEST-1"
    assert out["order"]["total_price"] == "100.00"


def test_get_order_returns_null_when_missing(seed_full: UnitOfWork) -> None:
    out = _call("get_order", order_id=9999)
    assert out == {"order": None}


# ---------------------------------------------------------------------------
# list_low_stock
# ---------------------------------------------------------------------------


def test_list_low_stock_default_threshold(seed_full: UnitOfWork) -> None:
    out = _call("list_low_stock")
    # available=3 < threshold=10 → returned.
    assert len(out["items"]) == 1
    assert out["items"][0]["available"] == 3  # noqa: PLR2004
    assert out["threshold"] == 10  # noqa: PLR2004


def test_list_low_stock_threshold_is_strict_lt(seed_full: UnitOfWork) -> None:
    # available=3, threshold=3 → NOT returned (strict <).
    out = _call("list_low_stock", threshold=3)
    assert out["items"] == []


# ---------------------------------------------------------------------------
# get_kpis
# ---------------------------------------------------------------------------


def test_get_kpis_returns_kpi_rows(seed_full: UnitOfWork) -> None:
    out = _call("get_kpis", since="2026-05-10", until="2026-05-10")
    assert len(out["items"]) == 1
    row = out["items"][0]
    assert row["sessions"] == 1000  # noqa: PLR2004
    assert row["revenue"] == "500.00"
    assert row["conversion_rate"] == "0.0250"


def test_get_kpis_accepts_relative_phrases(
    seed_full: UnitOfWork, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 'yesterday' resolves via the date normalizer; we just verify the call
    # doesn't crash. Returns 0 items because seeded kpi is at 2026-05-10.
    out = _call("get_kpis", since="yesterday", until="today")
    assert "items" in out


# ---------------------------------------------------------------------------
# compare_stores
# ---------------------------------------------------------------------------


def test_compare_stores_returns_per_store_rows(seed_full: UnitOfWork) -> None:
    out = _call(
        "compare_stores",
        since="2026-05-10T00:00:00Z",
        until="2026-05-11T00:00:00Z",
    )
    by_key = {r["store_key"]: r for r in out["rows"]}
    assert by_key["lubelife"]["paid_revenue"] == "100.00"
    assert by_key["lubelife"]["refunds_total"] == "25.00"
    assert by_key["lubelife"]["net_revenue"] == "75.00"
    assert by_key["shopjo"]["net_revenue"] == "200.00"
    assert out["currency_warning"] is False


def test_compare_stores_with_relative_window(seed_full: UnitOfWork) -> None:
    # 'last_week' / 'today' — verify they parse and the tool runs.
    out = _call("compare_stores", since="last_week", until="today")
    assert "rows" in out


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def test_tool_call_writes_audit_row(seed_full: UnitOfWork, fake_uow: UnitOfWork) -> None:
    _call("list_stores")
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert any(r.route_or_tool == "list_stores" and r.surface == "mcp" for r in rows)
