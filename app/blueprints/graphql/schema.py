"""Strawberry schema for /graphql (TR-33).

Types mirror the domain dataclasses; resolvers are thin wrappers over
the L4 services pulled from `current_app.extensions`. Money fields are
strings (decimal precision preserved end-to-end). NewType IDs collapse
to plain Int in the schema since GraphQL has no concept of NewType.

Filters are flat scalar args rather than input objects — the REST and
GraphQL surfaces converge on the same filter parameters, which keeps
client code symmetric.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import strawberry
from flask import current_app

from app.domain import models as dm
from app.domain.enums import FinancialStatus
from app.domain.specs import OrderSpec
from app.services.inventory_reporting import InventoryReportingService
from app.services.order_query import OrderQueryService
from app.services.store_compare import StoreComparisonService
from app.services.store_query import StoreQueryService

# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@strawberry.type
class Store:
    id: int
    store_key: str
    shop_domain: str
    display_name: str
    plus: bool
    active: bool
    currency_code: str | None


@strawberry.type
class OrderLineItem:
    id: int
    store_id: int
    sku: str | None
    title: str
    quantity: int
    price: str  # decimal as string for precision
    fulfillment_status: str | None


@strawberry.type
class Order:
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
    line_items: list[OrderLineItem]


@strawberry.type
class OrderPage:
    items: list[Order]
    next_cursor: str | None


@strawberry.type
class InventoryLevel:
    id: int
    store_id: int
    inventory_item_id: int
    location_id: int
    available: int | None
    on_hand: int | None
    committed: int | None
    incoming: int | None
    updated_at: datetime | None


@strawberry.type
class InventoryLevelPage:
    items: list[InventoryLevel]
    next_cursor: str | None


@strawberry.type
class StatusCount:
    status: str
    count: int


@strawberry.type
class StoreComparisonRow:
    store_id: int
    store_key: str
    order_count: int
    paid_revenue: str
    refunds_total: str
    net_revenue: str
    units_sold: int
    currency_code: str | None
    status_counts: list[StatusCount]


@strawberry.type
class StoreComparison:
    since: datetime
    until: datetime
    rows: list[StoreComparisonRow]
    currency_warning: bool


# ---------------------------------------------------------------------------
# Domain → schema converters
# ---------------------------------------------------------------------------


def _to_line_item(li: dm.OrderLineItem) -> OrderLineItem:
    return OrderLineItem(
        id=int(li.id),
        store_id=int(li.store_id),
        sku=li.sku,
        title=li.title,
        quantity=li.quantity,
        price=str(li.price),
        fulfillment_status=li.fulfillment_status.value if li.fulfillment_status else None,
    )


def _to_order(o: dm.Order) -> Order:
    return Order(
        id=int(o.id),
        store_id=int(o.store_id),
        gid=o.gid,
        name=o.name,
        email=o.email,
        financial_status=o.financial_status.value if o.financial_status else None,
        fulfillment_status=o.fulfillment_status.value if o.fulfillment_status else None,
        currency_code=o.currency_code,
        total_price=str(o.total_price),
        processed_at=o.processed_at,
        line_items=[_to_line_item(li) for li in o.line_items],
    )


def _to_store(s: dm.Store) -> Store:
    return Store(
        id=int(s.id),
        store_key=s.store_key,
        shop_domain=s.shop_domain,
        display_name=s.display_name,
        plus=s.plus,
        active=s.active,
        currency_code=s.currency_code,
    )


def _to_inv_level(lvl: dm.InventoryLevel) -> InventoryLevel:
    return InventoryLevel(
        id=int(lvl.id),
        store_id=int(lvl.store_id),
        inventory_item_id=int(lvl.inventory_item_id),
        location_id=int(lvl.location_id),
        available=lvl.available,
        on_hand=lvl.on_hand,
        committed=lvl.committed,
        incoming=lvl.incoming,
        updated_at=lvl.updated_at,
    )


def _to_comparison(c: dm.StoreComparison) -> StoreComparison:
    return StoreComparison(
        since=c.since,
        until=c.until,
        currency_warning=c.currency_warning,
        rows=[
            StoreComparisonRow(
                store_id=int(r.store_id),
                store_key=r.store_key,
                order_count=r.order_count,
                paid_revenue=str(r.paid_revenue),
                refunds_total=str(r.refunds_total),
                net_revenue=str(r.net_revenue),
                units_sold=r.units_sold,
                currency_code=r.currency_code,
                status_counts=[
                    StatusCount(status=status.value, count=count)
                    for status, count in r.status_counts.items()
                ],
            )
            for r in c.rows
        ],
    )


# ---------------------------------------------------------------------------
# Service accessors (raise on missing wiring — fail loud at first query)
# ---------------------------------------------------------------------------


def _order_query() -> OrderQueryService:
    svc = current_app.extensions.get("order_query_service")
    if svc is None:
        raise RuntimeError("order_query_service is not wired on this app")
    return cast(OrderQueryService, svc)


def _inventory_reporting() -> InventoryReportingService:
    svc = current_app.extensions.get("inventory_reporting_service")
    if svc is None:
        raise RuntimeError("inventory_reporting_service is not wired on this app")
    return cast(InventoryReportingService, svc)


def _store_comparison() -> StoreComparisonService:
    svc = current_app.extensions.get("store_comparison_service")
    if svc is None:
        raise RuntimeError("store_comparison_service is not wired on this app")
    return cast(StoreComparisonService, svc)


def _store_query() -> StoreQueryService:
    svc = current_app.extensions.get("store_query_service")
    if svc is None:
        raise RuntimeError("store_query_service is not wired on this app")
    return cast(StoreQueryService, svc)


def _store_ids_or_none(raw: list[int] | None) -> tuple[dm.StoreId, ...] | None:
    if not raw:
        return None
    return tuple(dm.StoreId(int(v)) for v in raw)


# ---------------------------------------------------------------------------
# Root Query
# ---------------------------------------------------------------------------


@strawberry.type
class Query:
    @strawberry.field
    def stores(self) -> list[Store]:
        """Active stores sorted by store_key."""
        return [_to_store(s) for s in _store_query().list_active()]

    @strawberry.field
    def order(self, id: int) -> Order | None:  # noqa: A002 — GraphQL convention
        """Fetch one order by its numeric DB id."""
        result = _order_query().get_order_by_id(dm.OrderId(id))
        return _to_order(result) if result is not None else None

    @strawberry.field
    def orders(  # noqa: PLR0913 — flat filter args mirror the REST surface
        self,
        store_ids: list[int] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        financial_status: str | None = None,
        sku: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderPage:
        """Paginated cross-store orders. Filters mirror /api/v1/orders."""
        spec = OrderSpec(
            store_ids=_store_ids_or_none(store_ids),
            since=since,
            until=until,
            financial_status=FinancialStatus(financial_status) if financial_status else None,
            sku=sku or None,
        )
        page = _order_query().list_orders(spec, limit=limit, cursor=cursor)
        return OrderPage(
            items=[_to_order(o) for o in page.items],
            next_cursor=page.next_cursor,
        )

    @strawberry.field
    def low_stock(  # noqa: PLR0913 — flat filter args mirror the REST surface
        self,
        store_ids: list[int] | None = None,
        threshold: int = 10,
        location_id: int | None = None,
        sku: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> InventoryLevelPage:
        """Inventory levels below `threshold`. Mirrors /api/v1/inventory/low-stock."""
        page = _inventory_reporting().list_low_stock(
            store_ids=_store_ids_or_none(store_ids),
            threshold=threshold,
            location_id=dm.LocationId(location_id) if location_id is not None else None,
            sku=sku or None,
            limit=limit,
            cursor=cursor,
        )
        return InventoryLevelPage(
            items=[_to_inv_level(lvl) for lvl in page.items],
            next_cursor=page.next_cursor,
        )

    @strawberry.field
    def compare_orders(
        self,
        since: datetime,
        until: datetime,
        store_ids: list[int] | None = None,
    ) -> StoreComparison:
        """Per-store revenue / refund / net rollup over `[since, until)`."""
        result = _store_comparison().compare_orders(
            since=since, until=until, store_ids=_store_ids_or_none(store_ids)
        )
        return _to_comparison(result)


schema = strawberry.Schema(query=Query)
