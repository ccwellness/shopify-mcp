"""Unit tests for the six MCP tools (TR-34 subset).

Drives the tools through `mcp.call_tool` so we exercise the same
Pydantic-bound argument coercion the LLM will hit. The container is
overridden to point at the in-memory persistence layer + a test-mode
container that skips Shopify credentials.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from dependency_injector import providers

# Tool registration happens on import:
import mcp_server.audit as audit_mod  # noqa: E402
import mcp_server.server as server_mod  # noqa: E402
import mcp_server.tools  # noqa: F401, E402
import mcp_server.tools.orders as orders_tool_mod  # noqa: E402
from app.container import Container
from app.domain.enums import (
    AnalyticsSource,
    FinancialStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
    ProductStatus,
    SubscriptionProvider,
    SubscriptionStatus,
    SyncResource,
)
from app.domain.models import (
    AnalyticsKpiDay,
    Customer,
    CustomerId,
    InventoryItem,
    InventoryItemId,
    InventoryLevel,
    InventoryLevelId,
    LocationId,
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    OrderShippingAddress,
    Product,
    ProductId,
    Refund,
    RefundId,
    SessionsDay,
    Store,
    StoreId,
    SubscriptionContract,
    SubscriptionContractId,
    Variant,
    VariantId,
)
from app.domain.repositories import UnitOfWork
from app.services.sync import SyncResult
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


# ---------------------------------------------------------------------------
# Builders for the second wave of tools
# ---------------------------------------------------------------------------


def _customer(*, cid: int, store_id: StoreId, email: str | None) -> Customer:
    return Customer(
        id=CustomerId(cid),
        store_id=store_id,
        gid=f"gid://shopify/Customer/{cid}",
        legacy_id=cid,
        email=email,
        phone=None,
        first_name=None,
        last_name=None,
        accepts_marketing=False,
        orders_count=0,
        total_spent=Decimal("0.00"),
        currency_code="USD",
        created_at=T0,
        updated_at=T0,
    )


def _variant(*, vid: int, product_id: int, store_id: StoreId) -> Variant:
    return Variant(
        id=VariantId(vid),
        store_id=store_id,
        product_id=ProductId(product_id),
        gid=f"gid://shopify/ProductVariant/{vid}",
        legacy_id=vid,
        title="Default",
        sku="SKU-V",
        barcode=None,
        position=1,
        price=Decimal("19.99"),
        compare_at_price=None,
        currency_code="USD",
        inventory_item_id=None,
    )


def _product(*, pid: int, store_id: StoreId, title: str = "Widget Pro") -> Product:
    return Product(
        id=ProductId(pid),
        store_id=store_id,
        gid=f"gid://shopify/Product/{pid}",
        legacy_id=pid,
        title=title,
        handle=f"widget-pro-{pid}",
        status=ProductStatus.ACTIVE,
        vendor="Acme",
        product_type="Gadget",
        tags=("new",),
        created_at=T0,
        updated_at=T0,
        variants=(_variant(vid=pid * 100 + 1, product_id=pid, store_id=store_id),),
    )


def _subscription(*, sid: int, store_id: StoreId, customer_id: int | None) -> SubscriptionContract:
    return SubscriptionContract(
        id=SubscriptionContractId(sid),
        store_id=store_id,
        customer_id=CustomerId(customer_id) if customer_id is not None else None,
        provider=SubscriptionProvider.ORDERGROOVE,
        provider_contract_id=f"og-{sid}",
        gid=None,
        legacy_id=sid,
        status=SubscriptionStatus.ACTIVE,
        next_billing_date=T0,
        frequency_interval="month",
        frequency_count=1,
        currency_code="USD",
        created_at=T0,
        updated_at=T0,
    )


@pytest.fixture
def seed_more(seed_full: UnitOfWork, fake_uow: UnitOfWork) -> UnitOfWork:
    """Add customers, products, subscriptions on top of the base seed."""
    with fake_uow as uow:
        uow.customers.upsert(_customer(cid=500, store_id=LUBELIFE, email="cust@example.com"))
        uow.products.upsert(_product(pid=900, store_id=LUBELIFE))
        uow.products.upsert(_product(pid=901, store_id=SHOPJO, title="Other Widget"))
        uow.subscriptions.upsert(_subscription(sid=7001, store_id=LUBELIFE, customer_id=500))
        uow.subscriptions.upsert(_subscription(sid=7002, store_id=SHOPJO, customer_id=None))
    return fake_uow


# ---------------------------------------------------------------------------
# list_subscriptions / get_subscription
# ---------------------------------------------------------------------------


def test_list_subscriptions_returns_cross_store(seed_more: UnitOfWork) -> None:
    out = _call("list_subscriptions")
    expected_rows = 2
    assert len(out["items"]) == expected_rows
    assert {s["provider"] for s in out["items"]} == {SubscriptionProvider.ORDERGROOVE.value}


def test_list_subscriptions_filters_by_store(seed_more: UnitOfWork) -> None:
    out = _call("list_subscriptions", store_id=[int(LUBELIFE)])
    assert [s["store_id"] for s in out["items"]] == [int(LUBELIFE)]


def test_get_subscription_returns_record(seed_more: UnitOfWork) -> None:
    out = _call("get_subscription", contract_id=7001)
    assert out["subscription"]["id"] == 7001  # noqa: PLR2004


def test_get_subscription_returns_null_when_missing(seed_more: UnitOfWork) -> None:
    out = _call("get_subscription", contract_id=9999)
    assert out == {"subscription": None}


# ---------------------------------------------------------------------------
# list_products / get_product
# ---------------------------------------------------------------------------


def test_list_products_returns_cross_store(seed_more: UnitOfWork) -> None:
    out = _call("list_products")
    expected_rows = 2
    assert len(out["items"]) == expected_rows


def test_list_products_filters_by_store(seed_more: UnitOfWork) -> None:
    out = _call("list_products", store_id=[int(LUBELIFE)])
    assert {p["store_id"] for p in out["items"]} == {int(LUBELIFE)}
    assert out["items"][0]["title"] == "Widget Pro"


def test_get_product_returns_detail_bundle(seed_more: UnitOfWork) -> None:
    out = _call("get_product", product_id=900)
    assert out["product"]["id"] == 900  # noqa: PLR2004
    # variants array carries through
    assert len(out["product"]["variants"]) == 1
    # detail bundle includes the fields the LLM relies on
    for key in ("inventory_levels", "sales_series", "recent_orders"):
        assert key in out


def test_get_product_returns_null_when_missing(seed_more: UnitOfWork) -> None:
    out = _call("get_product", product_id=9999)
    assert out["product"] is None


# ---------------------------------------------------------------------------
# search_orders_by_customer
# ---------------------------------------------------------------------------


def _order_for_customer(*, oid: int, customer_id: int | None, email: str | None) -> Order:
    base = _order(id=oid, store_id=LUBELIFE)
    return dataclasses.replace(base, customer_id=customer_id, email=email)


def test_search_orders_by_customer_requires_one_filter(seed_more: UnitOfWork) -> None:
    with pytest.raises(Exception, match="customer_id or email"):
        _call("search_orders_by_customer")


def test_search_orders_by_customer_id(seed_more: UnitOfWork, fake_uow: UnitOfWork) -> None:
    with fake_uow as uow:
        uow.orders.upsert(_order_for_customer(oid=3001, customer_id=500, email="cust@example.com"))
    out = _call("search_orders_by_customer", customer_id=500)
    assert {o["id"] for o in out["items"]} == {3001}


def test_search_orders_by_email(seed_more: UnitOfWork, fake_uow: UnitOfWork) -> None:
    with fake_uow as uow:
        uow.orders.upsert(_order_for_customer(oid=3002, customer_id=None, email="hit@example.com"))
    out = _call("search_orders_by_customer", email="hit@example.com")
    assert {o["id"] for o in out["items"]} == {3002}


# ---------------------------------------------------------------------------
# check_inventory
# ---------------------------------------------------------------------------


def test_check_inventory_requires_sku_or_location(seed_more: UnitOfWork) -> None:
    with pytest.raises(Exception, match="sku or location_id"):
        _call("check_inventory")


def test_check_inventory_by_sku(seed_more: UnitOfWork) -> None:
    # The seeded inventory item has SKU="SKU-A" (from _inv_item).
    out = _call("check_inventory", sku="SKU-A")
    assert len(out["items"]) == 1
    assert out["items"][0]["available"] == 3  # noqa: PLR2004


def test_check_inventory_by_location(seed_more: UnitOfWork) -> None:
    out = _call("check_inventory", location_id=10)
    assert len(out["items"]) == 1


# ---------------------------------------------------------------------------
# refresh_order — verify the tool wires through to SyncService.refresh_order
# without hitting Shopify. We stub the sync service via attribute replacement
# rather than the full DI container because the field is a plain dataclass slot.
# ---------------------------------------------------------------------------


class _StubSyncService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def refresh_order(self, store_key: str, order_gid: str) -> SyncResult:
        self.calls.append((store_key, order_gid))
        return SyncResult(store_key=store_key, resource=SyncResource.ORDERS, upserted=1)


def test_refresh_order_invokes_sync_and_returns_refreshed(
    seed_more: UnitOfWork, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubSyncService()
    real_services = server_mod.services

    def patched_services() -> object:
        bundle = real_services()
        # _Services is frozen + slotted; rebuild it with sync swapped out.
        return dataclasses.replace(bundle, sync=stub)  # type: ignore[type-var]

    # The tool module + the audit decorator each imported `services` by name,
    # so patch every reference rather than relying on attribute lookup.
    monkeypatch.setattr(server_mod, "services", patched_services)
    monkeypatch.setattr(audit_mod, "services", patched_services)
    monkeypatch.setattr(orders_tool_mod, "services", patched_services)

    gid = "gid://shopify/Order/1"
    out = _call("refresh_order", store_key="lubelife", order_gid=gid)
    assert stub.calls == [("lubelife", gid)]
    assert out["upserted"] == 1


# ---------------------------------------------------------------------------
# list_order_line_items — flat row-per-line-item export
# ---------------------------------------------------------------------------


def _shipping(*, order_id: int, store_id: StoreId, city: str = "Brooklyn") -> OrderShippingAddress:
    return OrderShippingAddress(
        order_id=OrderId(order_id),
        store_id=store_id,
        name="Jane Doe",
        company=None,
        address1="123 Main St",
        address2=None,
        city=city,
        province="NY",
        country="US",
        zip="11201",
        phone=None,
        latitude=None,
        longitude=None,
    )


def _customer(*, cid: int, store_id: StoreId, email: str = "jane@example.com") -> Customer:
    return Customer(
        id=CustomerId(cid),
        store_id=store_id,
        gid=f"gid://shopify/Customer/{cid}",
        legacy_id=cid,
        email=email,
        phone=None,
        first_name="Jane",
        last_name="Doe",
        accepts_marketing=False,
        orders_count=1,
        total_spent=Decimal("100.00"),
        currency_code="USD",
        created_at=T0,
        updated_at=T0,
    )


def _order_with(
    *,
    id: int,  # noqa: A002
    store_id: StoreId,
    line_items: tuple[OrderLineItem, ...] = (),
    shipping: OrderShippingAddress | None = None,
    customer_id: int | None = None,
) -> Order:
    base = _order(id=id, store_id=store_id)
    return dataclasses.replace(
        base,
        line_items=line_items,
        shipping_address=shipping,
        customer_id=CustomerId(customer_id) if customer_id is not None else None,
    )


def test_list_order_line_items_one_row_per_line_item(
    mcp_container: Container, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        # Order 1: two line items.
        li_a = _line_item(1, LUBELIFE, sku="SKU-A")
        li_b = dataclasses.replace(
            _line_item(1, LUBELIFE, sku="SKU-B"),
            id=OrderLineItemId(10_001 + 100),
        )
        uow.orders.upsert(_order_with(id=1, store_id=LUBELIFE, line_items=(li_a, li_b)))
        # Order 2: one line item.
        uow.orders.upsert(
            _order_with(id=2, store_id=LUBELIFE, line_items=(_line_item(2, LUBELIFE),))
        )

    out = _call("list_order_line_items", since="2026-05-09")
    expected_rows = 3
    expected_orders = 2
    assert out["rows_in_page"] == expected_rows
    assert out["orders_in_page"] == expected_orders
    skus = {r["sku"] for r in out["rows"]}
    assert skus == {"SKU-A", "SKU-B", "SKU-1"}


def test_list_order_line_items_filters_by_store_id(
    mcp_container: Container, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.stores.upsert(_store(sid=SHOPJO, key="shopjo"))
        uow.orders.upsert(
            _order_with(id=1, store_id=LUBELIFE, line_items=(_line_item(1, LUBELIFE),))
        )
        uow.orders.upsert(_order_with(id=2, store_id=SHOPJO, line_items=(_line_item(2, SHOPJO),)))

    out = _call("list_order_line_items", since="2026-05-09", store_id=[int(SHOPJO)])
    assert {r["store_id"] for r in out["rows"]} == {int(SHOPJO)}


def test_list_order_line_items_includes_shipping_and_customer(
    mcp_container: Container, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.customers.upsert(_customer(cid=42, store_id=LUBELIFE))
        uow.orders.upsert(
            _order_with(
                id=1,
                store_id=LUBELIFE,
                line_items=(_line_item(1, LUBELIFE),),
                shipping=_shipping(order_id=1, store_id=LUBELIFE, city="Brooklyn"),
                customer_id=42,
            )
        )
        # Order 2: no shipping, no customer.
        uow.orders.upsert(
            _order_with(id=2, store_id=LUBELIFE, line_items=(_line_item(2, LUBELIFE),))
        )

    out = _call("list_order_line_items", since="2026-05-09")
    by_order = {r["order_id"]: r for r in out["rows"]}
    assert by_order[1]["ship_city"] == "Brooklyn"
    assert by_order[1]["customer_email"] == "jane@example.com"
    assert by_order[1]["customer_first_name"] == "Jane"
    assert by_order[2]["ship_city"] is None
    assert by_order[2]["customer_email"] is None
    assert by_order[2]["customer_id"] is None


def test_list_order_line_items_since_is_required(
    mcp_container: Container, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))

    # FastMCP wraps the ValueError into a tool error — assert it doesn't succeed.
    with pytest.raises(Exception):  # noqa: B017, PT011
        _call("list_order_line_items", since="")


def test_list_order_line_items_accepts_last_month_phrase(
    mcp_container: Container, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.orders.upsert(
            _order_with(id=1, store_id=LUBELIFE, line_items=(_line_item(1, LUBELIFE),))
        )

    # 'last_month' resolves via parse_datetime. The seeded order is at T0
    # which is well within "last 30 days" of the test-running clock —
    # but we only assert the call doesn't raise. Row count depends on the
    # current real wall clock vs T0, so don't pin it.
    out = _call("list_order_line_items", since="last_month")
    assert "rows" in out


def test_list_order_line_items_pagination(mcp_container: Container, fake_uow: UnitOfWork) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        for oid in (1, 2, 3):
            uow.orders.upsert(
                _order_with(
                    id=oid,
                    store_id=LUBELIFE,
                    line_items=(_line_item(oid, LUBELIFE),),
                )
            )

    first = _call("list_order_line_items", since="2026-05-09", limit=2)
    expected_first = 2
    assert first["rows_in_page"] == expected_first
    assert first["next_cursor"] is not None

    second = _call(
        "list_order_line_items",
        since="2026-05-09",
        limit=2,
        cursor=first["next_cursor"],
    )
    assert second["rows_in_page"] == 1
    assert second["next_cursor"] is None


def test_list_order_line_items_line_extended_math(
    mcp_container: Container, fake_uow: UnitOfWork
) -> None:
    custom_line = OrderLineItem(
        id=OrderLineItemId(99_001),
        order_id=OrderId(1),
        store_id=LUBELIFE,
        variant_id=None,
        product_id=None,
        gid=None,
        legacy_id=None,
        title="Widget",
        sku="MATH-1",
        vendor=None,
        quantity=3,
        price=Decimal("10.00"),
        total_discount=Decimal("2.00"),
        fulfillment_status=None,
        requires_shipping=True,
        taxable=True,
    )
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.orders.upsert(_order_with(id=1, store_id=LUBELIFE, line_items=(custom_line,)))

    out = _call("list_order_line_items", since="2026-05-09")
    assert out["rows"][0]["line_extended"] == "28.00"


def test_list_order_line_items_empty_order_emits_one_row(
    mcp_container: Container, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.orders.upsert(_order_with(id=1, store_id=LUBELIFE, line_items=()))

    out = _call("list_order_line_items", since="2026-05-09")
    assert out["rows_in_page"] == 1
    assert out["orders_in_page"] == 1
    row = out["rows"][0]
    assert row["order_id"] == 1
    assert row["line_item_id"] is None
    assert row["sku"] is None
    assert row["quantity"] is None
    assert row["line_extended"] is None
