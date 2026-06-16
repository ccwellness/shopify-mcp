"""`list_products` + `get_product` MCP tools.

`get_product` returns the same bundled-detail shape as the REST endpoint
(product + variants + inventory levels + daily sales series + recent
orders) so an agent has one call to answer "tell me about product X".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import ProductStatus
from app.domain.models import ProductId, StoreId
from app.domain.specs import ProductSpec
from mcp_server.audit import audited
from mcp_server.dates import DateParseError, parse_datetime
from mcp_server.server import mcp, services

_MAX_LIMIT = 50
_DEFAULT_SALES_DAYS = 30


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class VariantOut(BaseModel):
    id: int
    title: str
    sku: str | None
    barcode: str | None
    position: int | None
    price: str
    compare_at_price: str | None
    currency_code: str | None
    inventory_item_id: int | None


class ProductOut(BaseModel):
    id: int
    store_id: int
    gid: str
    legacy_id: int
    title: str
    handle: str
    status: str
    vendor: str | None
    product_type: str | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    variants: list[VariantOut]


class ProductPageOut(BaseModel):
    items: list[ProductOut]
    next_cursor: str | None


class InventoryLevelOut(BaseModel):
    inventory_item_id: int
    location_id: int
    available: int | None
    on_hand: int | None
    committed: int | None
    incoming: int | None
    updated_at: datetime | None


class SalesDayOut(BaseModel):
    date: date
    units: int
    gross_revenue: str
    order_count: int


class RecentOrderOut(BaseModel):
    order_id: int
    name: str
    processed_at: datetime | None
    financial_status: str | None
    total_price: str
    currency_code: str
    units_of_product: int
    skus_of_product: list[str]


class ProductDetailOut(BaseModel):
    product: ProductOut | None
    inventory_levels: list[InventoryLevelOut]
    sales_series: list[SalesDayOut]
    recent_orders: list[RecentOrderOut]
    window_since: datetime | None
    window_until: datetime | None


def _to_variant(v: Any) -> VariantOut:
    return VariantOut(
        id=int(v.id),
        title=v.title,
        sku=v.sku,
        barcode=v.barcode,
        position=v.position,
        price=str(v.price),
        compare_at_price=str(v.compare_at_price) if v.compare_at_price is not None else None,
        currency_code=v.currency_code,
        inventory_item_id=int(v.inventory_item_id) if v.inventory_item_id is not None else None,
    )


def _to_product(p: Any) -> ProductOut:
    return ProductOut(
        id=int(p.id),
        store_id=int(p.store_id),
        gid=p.gid,
        legacy_id=p.legacy_id,
        title=p.title,
        handle=p.handle,
        status=p.status.value,
        vendor=p.vendor,
        product_type=p.product_type,
        tags=list(p.tags),
        created_at=p.created_at,
        updated_at=p.updated_at,
        variants=[_to_variant(v) for v in p.variants],
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool
@audited("list_products")
def list_products(  # noqa: PLR0913 — flat filter args mirror REST + GraphQL
    store_id: list[int] | None = Field(  # noqa: B008
        default=None, description="Optional list of numeric store ids."
    ),
    status: str | None = Field(default=None, description="One of: active, archived, draft."),
    title_query: str | None = Field(
        default=None, description="Substring match on the product title (case-insensitive)."
    ),
    vendor: str | None = Field(default=None, description="Exact-match vendor."),
    product_type: str | None = Field(default=None, description="Exact-match product type."),
    tag: str | None = Field(default=None, description="Filter to products carrying this tag."),
    limit: int = Field(default=50, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Field(default=None),
) -> ProductPageOut:
    """Paginated cross-store product catalog. Sorts by updated_at desc.

    Mirrors GET /api/v1/products. Use `get_product` after picking an id.
    """
    spec = ProductSpec(
        store_ids=tuple(StoreId(s) for s in store_id) if store_id else None,
        status=ProductStatus(status) if status else None,
        title_query=title_query or None,
        vendor=vendor or None,
        product_type=product_type or None,
        tag=tag or None,
    )
    page = services().products.list_products(spec, limit=limit, cursor=cursor)
    return ProductPageOut(
        items=[_to_product(p) for p in page.items],
        next_cursor=page.next_cursor,
    )


@mcp.tool
@audited("get_product")
def get_product(
    product_id: int = Field(description="Numeric DB id of the product (from list_products)."),
    since: str | None = Field(
        default=None,
        description=(
            "Sales-series window start. ISO 8601 or relative ('7d', 'last_month'). "
            "Defaults to 30 days ago."
        ),
    ),
    until: str | None = Field(
        default=None,
        description="Sales-series window end. Defaults to now (UTC).",
    ),
) -> ProductDetailOut:
    """Full product detail: variants, current inventory per variant, daily
    sales series, and the 20 most recent orders containing it.

    Sales-series window defaults to trailing 30 days. Pass `since` / `until`
    to widen or narrow it.
    """
    svc = services().products
    product = svc.get_product_by_id(ProductId(product_id))
    if product is None:
        return ProductDetailOut(
            product=None,
            inventory_levels=[],
            sales_series=[],
            recent_orders=[],
            window_since=None,
            window_until=None,
        )

    try:
        until_dt = parse_datetime(until) or datetime.now(tz=UTC)
        since_dt = parse_datetime(since) or until_dt - timedelta(days=_DEFAULT_SALES_DAYS)
    except DateParseError as exc:
        raise ValueError(str(exc)) from exc

    variant_ids = tuple(v.id for v in product.variants)
    levels = svc.get_inventory_for_variants(product.store_id, variant_ids) if variant_ids else ()
    sales = svc.get_sales_by_day(product.store_id, ProductId(product_id), since_dt, until_dt)
    recent = svc.get_recent_orders(product.store_id, ProductId(product_id))

    return ProductDetailOut(
        product=_to_product(product),
        inventory_levels=[
            InventoryLevelOut(
                inventory_item_id=int(lv.inventory_item_id),
                location_id=int(lv.location_id),
                available=lv.available,
                on_hand=lv.on_hand,
                committed=lv.committed,
                incoming=lv.incoming,
                updated_at=lv.updated_at,
            )
            for lv in levels
        ],
        sales_series=[
            SalesDayOut(
                date=d.date,
                units=d.units,
                gross_revenue=str(d.gross_revenue),
                order_count=d.order_count,
            )
            for d in sales
        ],
        recent_orders=[
            RecentOrderOut(
                order_id=int(r.order.id),
                name=r.order.name,
                processed_at=r.order.processed_at,
                financial_status=(
                    r.order.financial_status.value if r.order.financial_status else None
                ),
                total_price=str(r.order.total_price),
                currency_code=r.order.currency_code,
                units_of_product=r.units_of_product,
                skus_of_product=list(r.skus_of_product),
            )
            for r in recent
        ],
        window_since=since_dt,
        window_until=until_dt,
    )
