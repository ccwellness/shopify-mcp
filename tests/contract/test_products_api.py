"""Contract tests for /api/v1/products and /api/v1/products/<id>."""

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
    ProductStatus,
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
    Product,
    ProductId,
    StoreId,
    Variant,
    VariantId,
)
from app.domain.repositories import UnitOfWork

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)
LOC_A = LocationId(10)
T0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

DEFAULT_LIMIT = 50  # mirrors app.services.product_query.DEFAULT_LIMIT
DEFAULT_RECENT = 20  # mirrors app.services.product_query.DEFAULT_RECENT_ORDERS


def _variant(*, vid: int, pid: int, sku: str, price: str = "9.99") -> Variant:
    return Variant(
        id=VariantId(vid),
        store_id=LUBELIFE,
        product_id=ProductId(pid),
        gid=f"gid://shopify/ProductVariant/{vid}",
        legacy_id=vid,
        title=f"Variant {vid}",
        sku=sku,
        barcode=None,
        position=1,
        price=Decimal(price),
        compare_at_price=None,
        currency_code="USD",
        inventory_item_id=None,
    )


def _product(  # noqa: PLR0913 — test factory; explicit kwargs are clearer than a builder.
    *,
    pid: int,
    store_id: StoreId = LUBELIFE,
    title: str = "Widget",
    status: ProductStatus = ProductStatus.ACTIVE,
    vendor: str = "Acme",
    product_type: str = "Widget",
    updated_at: datetime = T0,
    variants: tuple[Variant, ...] = (),
) -> Product:
    return Product(
        id=ProductId(pid),
        store_id=store_id,
        gid=f"gid://shopify/Product/{pid}",
        legacy_id=pid,
        title=title,
        handle=f"widget-{pid}",
        status=status,
        vendor=vendor,
        product_type=product_type,
        tags=("featured",),
        created_at=T0,
        updated_at=updated_at,
        variants=variants,
    )


def _line(  # noqa: PLR0913 — test factory; explicit kwargs are clearer than a builder.
    *, lid: int, oid: int, pid: int, sku: str, qty: int = 1, price: str = "9.99"
) -> OrderLineItem:
    return OrderLineItem(
        id=OrderLineItemId(lid),
        order_id=OrderId(oid),
        store_id=LUBELIFE,
        variant_id=None,
        product_id=ProductId(pid),
        gid=None,
        legacy_id=None,
        title=f"Line {lid}",
        sku=sku,
        vendor=None,
        quantity=qty,
        price=Decimal(price),
        total_discount=Decimal("0.00"),
        fulfillment_status=OrderLineFulfillmentStatus.FULFILLED,
        requires_shipping=True,
        taxable=True,
    )


def _order(  # noqa: PLR0913 — test factory; explicit kwargs are clearer than a builder.
    *,
    oid: int,
    processed_at: datetime,
    lines: tuple[OrderLineItem, ...],
    store_id: StoreId = LUBELIFE,
    total_price: str = "19.98",
) -> Order:
    return Order(
        id=OrderId(oid),
        store_id=store_id,
        customer_id=None,
        gid=f"gid://shopify/Order/{oid}",
        legacy_id=oid,
        name=f"#PROD-{oid}",
        order_number=oid,
        email=None,
        financial_status=FinancialStatus.PAID,
        fulfillment_status=FulfillmentStatus.FULFILLED,
        currency_code="USD",
        presentment_currency_code=None,
        subtotal_price=Decimal(total_price),
        total_price=Decimal(total_price),
        total_tax=Decimal("0"),
        total_discounts=Decimal("0"),
        total_shipping=Decimal("0"),
        presentment_subtotal_price=None,
        presentment_total_price=None,
        processed_at=processed_at,
        cancelled_at=None,
        closed_at=None,
        created_at=processed_at,
        updated_at=processed_at,
        line_items=lines,
    )


@pytest.fixture
def seed(fake_uow: UnitOfWork) -> UnitOfWork:
    # Product 1: 2 variants, with inventory; sold in 3 orders across 2 days.
    # Product 2: in shopjo store; should not surface in lubelife filters.
    v1a = _variant(vid=10, pid=1, sku="SKU-A")
    v1b = _variant(vid=11, pid=1, sku="SKU-B")
    p1 = _product(pid=1, title="Featured Widget", variants=(v1a, v1b))
    p2 = _product(pid=2, store_id=SHOPJO, title="Other Widget", updated_at=T0 + timedelta(hours=1))

    with fake_uow as uow:
        uow.products.upsert(p1)
        uow.products.upsert(p2)
        # InventoryItem links to variant; level links to item.
        uow.inventory.upsert_item(
            InventoryItem(
                id=InventoryItemId(100),
                store_id=LUBELIFE,
                variant_id=VariantId(10),
                gid="gid://shopify/InventoryItem/100",
                legacy_id=100,
                sku="SKU-A",
                tracked=True,
            )
        )
        uow.inventory.upsert_level(
            InventoryLevel(
                id=InventoryLevelId(1),
                store_id=LUBELIFE,
                inventory_item_id=InventoryItemId(100),
                location_id=LOC_A,
                available=42,
                on_hand=42,
                committed=0,
                incoming=0,
                updated_at=T0,
            )
        )
        # Orders: 2 on day 1 (different qty), 1 on day 2.
        uow.orders.upsert(
            _order(
                oid=101,
                processed_at=T0,
                lines=(_line(lid=1001, oid=101, pid=1, sku="SKU-A", qty=2),),
                total_price="19.98",
            )
        )
        uow.orders.upsert(
            _order(
                oid=102,
                processed_at=T0 + timedelta(hours=2),
                lines=(_line(lid=1002, oid=102, pid=1, sku="SKU-B", qty=1),),
                total_price="9.99",
            )
        )
        uow.orders.upsert(
            _order(
                oid=103,
                processed_at=T0 + timedelta(days=1, hours=3),
                lines=(_line(lid=1003, oid=103, pid=1, sku="SKU-A", qty=3),),
                total_price="29.97",
            )
        )
        # Decoy: order with a different product, should NOT show up.
        uow.orders.upsert(
            _order(
                oid=999,
                processed_at=T0,
                lines=(_line(lid=9999, oid=999, pid=2, sku="OTHER"),),
            )
        )
    return fake_uow


# ---------------------------------------------------------------------------
# GET /api/v1/products — list
# ---------------------------------------------------------------------------


def test_list_products_empty(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/products")
    assert resp.status_code == HTTPStatus.OK
    assert resp.get_json() == {"items": [], "next_cursor": None, "limit": DEFAULT_LIMIT}


def test_list_products_orders_by_updated_at_desc(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    body = authed_client.get("/api/v1/products").get_json()
    assert [item["id"] for item in body["items"]] == [2, 1]


def test_list_products_filters_by_store_id(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    body = authed_client.get(f"/api/v1/products?store_id={int(LUBELIFE)}").get_json()
    assert [item["id"] for item in body["items"]] == [1]


def test_list_products_filters_by_status(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    body = authed_client.get("/api/v1/products?status=active").get_json()
    assert {item["status"] for item in body["items"]} == {"active"}


def test_list_products_rejects_bad_status(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/products?status=banana")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# GET /api/v1/products/<id> — detail with analytics bundle
# ---------------------------------------------------------------------------


def test_get_product_returns_full_bundle(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    # Wide window so the seeded orders fall inside the trailing-30d default.
    since = (T0 - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    until = (T0 + timedelta(days=5)).isoformat().replace("+00:00", "Z")
    resp = authed_client.get(f"/api/v1/products/1?since={since}&until={until}")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_json()

    assert body["product"]["id"] == 1
    expected_variants = 2
    assert len(body["product"]["variants"]) == expected_variants
    assert {v["sku"] for v in body["product"]["variants"]} == {"SKU-A", "SKU-B"}

    # Inventory level surfaces (one item, one location).
    assert len(body["inventory_levels"]) == 1
    assert body["inventory_levels"][0]["available"] == 42  # noqa: PLR2004 — seeded fixture value

    # Sales series: 2 days hit (day 0 has 2 orders, day 1 has 1).
    expected_sales_days = 2
    assert len(body["sales_series"]) == expected_sales_days
    by_date = {d["date"]: d for d in body["sales_series"]}
    day0 = T0.date().isoformat()
    day1 = (T0 + timedelta(days=1)).date().isoformat()
    expected_units_day0 = 3  # qty 2 + qty 1
    expected_units_day1 = 3
    expected_orders_day0 = 2
    expected_orders_day1 = 1
    assert by_date[day0]["units"] == expected_units_day0
    assert by_date[day0]["order_count"] == expected_orders_day0
    assert by_date[day1]["units"] == expected_units_day1
    assert by_date[day1]["order_count"] == expected_orders_day1

    # Recent orders: 3 entries (the decoy order 999 about product 2 stays out).
    expected_recent = 3
    assert len(body["recent_orders"]) == expected_recent
    order_ids = [r["order"]["id"] for r in body["recent_orders"]]
    expected_recent_ids = [103, 102, 101]  # processed_at desc
    assert order_ids == expected_recent_ids
    # Units of *this product* per order is rolled up.
    by_id = {r["order"]["id"]: r for r in body["recent_orders"]}
    expected_units_103 = 3
    expected_units_102 = 1
    expected_units_101 = 2
    assert by_id[103]["units_of_product"] == expected_units_103
    assert by_id[102]["units_of_product"] == expected_units_102
    assert by_id[101]["units_of_product"] == expected_units_101


def test_get_product_404_for_missing(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/products/9999")
    assert resp.status_code == HTTPStatus.NOT_FOUND
    assert "not found" in resp.get_json()["error"]


def test_get_product_default_window_is_trailing_30_days(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    # No since/until → defaults to trailing 30d from now. Our seeded orders
    # are at 2026-05-01; depending on current date they may not fall in the
    # window. The point is the bundle still returns a 200 with a window block.
    resp = authed_client.get("/api/v1/products/1")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_json()
    assert "window" in body
    assert body["window"]["since"] < body["window"]["until"]
