"""`list_orders`, `get_order`, `refresh_order`, `search_orders_by_customer`,
`list_order_line_items` MCP tools."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.enums import FinancialStatus, FulfillmentStatus
from app.domain.models import CustomerId, OrderId, StoreId
from app.domain.specs import OrderSpec
from mcp_server.audit import audited
from mcp_server.dates import DateParseError, parse_datetime
from mcp_server.server import mcp, services

_MAX_LIMIT = 50
_ROW_MAX_LIMIT = 200


class LineItemOut(BaseModel):
    id: int
    sku: str | None
    title: str
    quantity: int
    price: str  # decimal as string for precision


class OrderOut(BaseModel):
    id: int
    store_id: int
    gid: str
    name: str
    email: str | None
    financial_status: str | None
    fulfillment_status: str | None
    currency_code: str
    total_price: str
    processed_at: datetime | None
    line_items: list[LineItemOut]


class OrderPageOut(BaseModel):
    items: list[OrderOut]
    next_cursor: str | None


class GetOrderOut(BaseModel):
    """Wrapper for `get_order` so the tool always returns a dict-shaped result.

    Returning `OrderOut | None` directly would force FastMCP to wrap the
    result in `{"result": ...}` for None compatibility; an explicit
    `order` field reads cleaner to the LLM.
    """

    order: OrderOut | None


def _to_line_item(li: object) -> LineItemOut:
    return LineItemOut(
        id=int(li.id),  # type: ignore[attr-defined]
        sku=li.sku,  # type: ignore[attr-defined]
        title=li.title,  # type: ignore[attr-defined]
        quantity=li.quantity,  # type: ignore[attr-defined]
        price=str(li.price),  # type: ignore[attr-defined]
    )


def _to_order(o: object) -> OrderOut:
    return OrderOut(
        id=int(o.id),  # type: ignore[attr-defined]
        store_id=int(o.store_id),  # type: ignore[attr-defined]
        gid=o.gid,  # type: ignore[attr-defined]
        name=o.name,  # type: ignore[attr-defined]
        email=o.email,  # type: ignore[attr-defined]
        financial_status=(  # type: ignore[attr-defined]
            o.financial_status.value if o.financial_status else None  # type: ignore[attr-defined]
        ),
        fulfillment_status=(  # type: ignore[attr-defined]
            o.fulfillment_status.value if o.fulfillment_status else None  # type: ignore[attr-defined]
        ),
        currency_code=o.currency_code,  # type: ignore[attr-defined]
        total_price=str(o.total_price),  # type: ignore[attr-defined]
        processed_at=o.processed_at,  # type: ignore[attr-defined]
        line_items=[_to_line_item(li) for li in o.line_items],  # type: ignore[attr-defined]
    )


@mcp.tool
@audited("list_orders")
def list_orders(  # noqa: PLR0913 — flat filter args mirror REST + GraphQL
    store_id: list[int] | None = Field(  # noqa: B008 — Pydantic Field-as-default is the idiom
        default=None, description="Optional list of numeric store ids to filter to."
    ),
    since: str | None = Field(
        default=None,
        description="ISO 8601 or relative phrase ('yesterday', '7d', 'last_week').",
    ),
    until: str | None = Field(
        default=None,
        description="ISO 8601 or relative phrase.",
    ),
    financial_status: str | None = Field(
        default=None,
        description=(
            "One of: pending, authorized, partially_paid, paid, "
            "partially_refunded, refunded, voided, expired."
        ),
    ),
    sku: str | None = Field(default=None, description="Match orders containing this SKU."),
    limit: int = Field(default=50, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Field(default=None, description="Opaque next_cursor from a prior page."),
) -> OrderPageOut:
    """Paginated cross-store order list. Mirrors GET /api/v1/orders.

    Filters compose: providing several narrows the result. Returns at
    most 50 rows per call; call again with `cursor=next_cursor` for more.
    """
    try:
        since_dt = parse_datetime(since)
        until_dt = parse_datetime(until)
    except DateParseError as exc:
        raise ValueError(str(exc)) from exc

    spec = OrderSpec(
        store_ids=tuple(StoreId(s) for s in store_id) if store_id else None,
        since=since_dt,
        until=until_dt,
        financial_status=FinancialStatus(financial_status) if financial_status else None,
        sku=sku or None,
    )
    page = services().orders.list_orders(spec, limit=limit, cursor=cursor)
    return OrderPageOut(
        items=[_to_order(o) for o in page.items],
        next_cursor=page.next_cursor,
    )


@mcp.tool
@audited("get_order")
def get_order(
    order_id: int = Field(description="Numeric DB id of the order (from list_orders)."),
) -> GetOrderOut:
    """Fetch full order detail by numeric id. `order` is null if not found.

    Use list_orders first to discover ids; pass the integer `id` field
    here, not the Shopify GID.
    """
    o = services().orders.get_order_by_id(OrderId(order_id))
    return GetOrderOut(order=_to_order(o) if o is not None else None)


class RefreshOrderOut(BaseModel):
    """Result of a single-order refresh.

    `order` is the freshly-upserted record fetched after the GraphQL pull,
    or null if Shopify returned no order for the GID.
    """

    store_key: str
    upserted: int
    order: OrderOut | None


@mcp.tool
@audited("refresh_order")
def refresh_order(
    store_key: str = Field(description="Store key, e.g. 'lubelife' or 'shopjo'."),
    order_gid: str = Field(
        description="Full Shopify order GID, e.g. 'gid://shopify/Order/7117040550127'.",
    ),
) -> RefreshOrderOut:
    """Re-fetch one order from Shopify and upsert it.

    Use when the cached order looks stale — for example after a manual
    refund or a status change you suspect hasn't replicated yet. Returns
    the refreshed order so the caller doesn't need a follow-up `get_order`.
    """
    svc = services()
    result = svc.sync.refresh_order(store_key, order_gid)

    store = next((s for s in svc.stores.list_active() if s.store_key == store_key), None)
    refreshed = svc.orders.get_order_by_gid(store.id, order_gid) if store is not None else None
    return RefreshOrderOut(
        store_key=result.store_key,
        upserted=result.upserted,
        order=_to_order(refreshed) if refreshed is not None else None,
    )


@mcp.tool
@audited("search_orders_by_customer")
def search_orders_by_customer(
    customer_id: int | None = Field(
        default=None,
        description="Numeric internal customer id. Either this or `email` must be set.",
    ),
    email: str | None = Field(
        default=None,
        description=(
            "Customer email — matches the order's email field OR the linked "
            "customer's stored email. Either this or `customer_id` must be set."
        ),
    ),
    store_id: list[int] | None = Field(  # noqa: B008
        default=None, description="Optional list of numeric store ids to restrict the search."
    ),
    limit: int = Field(default=50, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Field(default=None),
) -> OrderPageOut:
    """Find every order placed by a customer, across stores.

    Pass either `customer_id` (preferred — exact match on our internal id)
    or `email` (matches either order.email or the linked customer's email).
    Sorts by processed_at desc.
    """
    if customer_id is None and not email:
        raise ValueError("either customer_id or email is required")

    spec = OrderSpec(
        store_ids=tuple(StoreId(s) for s in store_id) if store_id else None,
        customer_id=CustomerId(customer_id) if customer_id is not None else None,
        customer_email=email or None,
    )
    page = services().orders.list_orders(spec, limit=limit, cursor=cursor)
    return OrderPageOut(
        items=[_to_order(o) for o in page.items],
        next_cursor=page.next_cursor,
    )


# ---------------------------------------------------------------------------
# Flat, one-row-per-line-item export — `list_order_line_items`
# ---------------------------------------------------------------------------


class OrderLineRowOut(BaseModel):
    """One denormalized row: order context + single line item + shipping + customer.

    All money fields are decimal-as-string. Line-item fields are null when
    the order has zero line items (rare — refund-only / test orders).
    Shipping fields are null for digital/pickup orders. Customer fields
    are null when no Customer row is linked to the order.
    """

    order_id: int
    order_name: str
    order_number: int | None
    order_gid: str
    store_id: int
    processed_at: datetime
    created_at: datetime
    financial_status: str | None
    fulfillment_status: str | None
    currency_code: str
    order_subtotal: str
    order_total: str
    order_total_tax: str
    order_total_discounts: str
    order_total_shipping: str
    source_name: str | None
    line_item_id: int | None
    line_item_gid: str | None
    line_item_title: str | None
    sku: str | None
    vendor: str | None
    variant_id: int | None
    product_id: int | None
    quantity: int | None
    unit_price: str | None
    line_total_discount: str | None
    line_extended: str | None
    line_fulfillment_status: str | None
    requires_shipping: bool | None
    taxable: bool | None
    ship_name: str | None
    ship_company: str | None
    ship_address1: str | None
    ship_address2: str | None
    ship_city: str | None
    ship_province: str | None
    ship_country: str | None
    ship_zip: str | None
    ship_phone: str | None
    ship_latitude: str | None
    ship_longitude: str | None
    customer_id: int | None
    customer_email: str | None
    customer_first_name: str | None
    customer_last_name: str | None
    customer_phone: str | None


class OrderLineRowPageOut(BaseModel):
    """One page of flat order-line rows plus progress counts.

    `limit` controls orders per page, not rows — a multi-line cart yields
    multiple rows per order. Use `next_cursor` to page; `orders_in_page`
    and `rows_in_page` let the caller reason about progress.
    """

    rows: list[OrderLineRowOut]
    next_cursor: str | None
    orders_in_page: int
    rows_in_page: int


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _to_row(r: object) -> OrderLineRowOut:
    return OrderLineRowOut(
        order_id=int(r.order_id),  # type: ignore[attr-defined]
        order_name=r.order_name,  # type: ignore[attr-defined]
        order_number=r.order_number,  # type: ignore[attr-defined]
        order_gid=r.order_gid,  # type: ignore[attr-defined]
        store_id=int(r.store_id),  # type: ignore[attr-defined]
        processed_at=r.processed_at,  # type: ignore[attr-defined]
        created_at=r.created_at,  # type: ignore[attr-defined]
        financial_status=(  # type: ignore[attr-defined]
            r.financial_status.value if r.financial_status else None  # type: ignore[attr-defined]
        ),
        fulfillment_status=(  # type: ignore[attr-defined]
            r.fulfillment_status.value if r.fulfillment_status else None  # type: ignore[attr-defined]
        ),
        currency_code=r.currency_code,  # type: ignore[attr-defined]
        order_subtotal=str(r.order_subtotal),  # type: ignore[attr-defined]
        order_total=str(r.order_total),  # type: ignore[attr-defined]
        order_total_tax=str(r.order_total_tax),  # type: ignore[attr-defined]
        order_total_discounts=str(r.order_total_discounts),  # type: ignore[attr-defined]
        order_total_shipping=str(r.order_total_shipping),  # type: ignore[attr-defined]
        source_name=r.source_name,  # type: ignore[attr-defined]
        line_item_id=int(r.line_item_id) if r.line_item_id is not None else None,  # type: ignore[attr-defined]
        line_item_gid=r.line_item_gid,  # type: ignore[attr-defined]
        line_item_title=r.line_item_title,  # type: ignore[attr-defined]
        sku=r.sku,  # type: ignore[attr-defined]
        vendor=r.vendor,  # type: ignore[attr-defined]
        variant_id=int(r.variant_id) if r.variant_id is not None else None,  # type: ignore[attr-defined]
        product_id=int(r.product_id) if r.product_id is not None else None,  # type: ignore[attr-defined]
        quantity=r.quantity,  # type: ignore[attr-defined]
        unit_price=_money(r.unit_price),  # type: ignore[attr-defined]
        line_total_discount=_money(r.line_total_discount),  # type: ignore[attr-defined]
        line_extended=_money(r.line_extended),  # type: ignore[attr-defined]
        line_fulfillment_status=(  # type: ignore[attr-defined]
            r.line_fulfillment_status.value  # type: ignore[attr-defined]
            if r.line_fulfillment_status
            else None
        ),
        requires_shipping=r.requires_shipping,  # type: ignore[attr-defined]
        taxable=r.taxable,  # type: ignore[attr-defined]
        ship_name=r.ship_name,  # type: ignore[attr-defined]
        ship_company=r.ship_company,  # type: ignore[attr-defined]
        ship_address1=r.ship_address1,  # type: ignore[attr-defined]
        ship_address2=r.ship_address2,  # type: ignore[attr-defined]
        ship_city=r.ship_city,  # type: ignore[attr-defined]
        ship_province=r.ship_province,  # type: ignore[attr-defined]
        ship_country=r.ship_country,  # type: ignore[attr-defined]
        ship_zip=r.ship_zip,  # type: ignore[attr-defined]
        ship_phone=r.ship_phone,  # type: ignore[attr-defined]
        ship_latitude=_money(r.ship_latitude),  # type: ignore[attr-defined]
        ship_longitude=_money(r.ship_longitude),  # type: ignore[attr-defined]
        customer_id=int(r.customer_id) if r.customer_id is not None else None,  # type: ignore[attr-defined]
        customer_email=r.customer_email,  # type: ignore[attr-defined]
        customer_first_name=r.customer_first_name,  # type: ignore[attr-defined]
        customer_last_name=r.customer_last_name,  # type: ignore[attr-defined]
        customer_phone=r.customer_phone,  # type: ignore[attr-defined]
    )


@mcp.tool
@audited("list_order_line_items")
def list_order_line_items(  # noqa: PLR0913 — flat filter args mirror list_orders
    since: str = Field(
        description=(
            "Window start (REQUIRED). ISO 8601 or relative phrase: "
            "'last_month', '30d', 'yesterday', '2026-04-01', etc. "
            "Without a window this tool would happily try to dump every "
            "order ever — keep it bounded."
        ),
    ),
    until: str | None = Field(
        default=None,
        description="Window end. Defaults to now (UTC) when omitted.",
    ),
    store_id: list[int] | None = Field(  # noqa: B008
        default=None, description="Optional list of numeric store ids to filter to."
    ),
    financial_status: str | None = Field(
        default=None,
        description=(
            "One of: pending, authorized, partially_paid, paid, "
            "partially_refunded, refunded, voided, expired."
        ),
    ),
    fulfillment_status: str | None = Field(
        default=None,
        description="One of: fulfilled, partial, unfulfilled, restocked.",
    ),
    sku: str | None = Field(
        default=None,
        description=(
            "Restrict to orders containing this SKU. Note: the row set "
            "still contains every line item on each matched order, not "
            "only the matching SKU."
        ),
    ),
    customer_id: int | None = Field(
        default=None, description="Restrict to one customer's orders by numeric id."
    ),
    customer_email: str | None = Field(
        default=None, description="Restrict to one customer by email (exact match)."
    ),
    min_total: str | None = Field(
        default=None,
        description="Decimal string. Restrict to orders with total_price >= min_total.",
    ),
    tag: str | None = Field(default=None, description="Restrict to orders carrying this tag."),
    limit: int = Field(
        default=50,
        ge=1,
        le=_ROW_MAX_LIMIT,
        description=(
            "Max ORDERS per page (clamped 1..200). Row count per page is "
            "limit × avg_lines_per_order; typically 1-5× the order count."
        ),
    ),
    cursor: str | None = Field(default=None, description="Opaque next_cursor from a prior page."),
) -> OrderLineRowPageOut:
    """One flat row per order line item across the matched orders.

    Use for export-style queries like "all order information for last
    month" — each row carries the order context, the single line item,
    the shipping address, and the linked customer. Money fields are
    decimal strings. Orders with zero line items emit a single row with
    `line_item_*` fields = null so row counts reconcile to "orders
    touched". `line_extended = quantity * unit_price - line_total_discount`
    is precomputed gross of refunds — refunds are not netted in this
    schema.

    Pagination is order-grained: `limit` is the max orders per page, and
    one page may yield several times that many rows. `next_cursor`
    advances by the same keyset (`processed_at` desc, `id` desc) used by
    `list_orders`.

    `since` is REQUIRED — pass an explicit window. Accepted phrases:
    'last_month', 'last_week', '7d', 'yesterday', '2026-04-01',
    '2026-04-01T00:00:00Z'.
    """
    if not since or not since.strip():
        raise ValueError("since is required (e.g. 'last_month', '30d', or an ISO date)")
    try:
        since_dt = parse_datetime(since)
        until_dt = parse_datetime(until)
    except DateParseError as exc:
        raise ValueError(str(exc)) from exc
    if since_dt is None:
        raise ValueError("since is required (parsed to None)")

    try:
        min_total_dec = Decimal(min_total) if min_total else None
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"min_total must be a decimal string, got {min_total!r}") from exc

    spec = OrderSpec(
        store_ids=tuple(StoreId(s) for s in store_id) if store_id else None,
        since=since_dt,
        until=until_dt,
        financial_status=FinancialStatus(financial_status) if financial_status else None,
        fulfillment_status=FulfillmentStatus(fulfillment_status) if fulfillment_status else None,
        sku=sku or None,
        customer_id=CustomerId(customer_id) if customer_id is not None else None,
        customer_email=customer_email or None,
        min_total=min_total_dec,
        tag=tag or None,
    )

    page = services().orders.list_order_line_rows(spec, limit=limit, cursor=cursor)
    rows = [_to_row(r) for r in page.items]
    orders_in_page = len({r.order_id for r in rows})
    return OrderLineRowPageOut(
        rows=rows,
        next_cursor=page.next_cursor,
        orders_in_page=orders_in_page,
        rows_in_page=len(rows),
    )
