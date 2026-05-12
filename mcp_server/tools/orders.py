"""`list_orders` + `get_order` MCP tools."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import FinancialStatus
from app.domain.models import OrderId, StoreId
from app.domain.specs import OrderSpec
from mcp_server.audit import audited
from mcp_server.dates import DateParseError, parse_datetime
from mcp_server.server import mcp, services

_MAX_LIMIT = 50


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
