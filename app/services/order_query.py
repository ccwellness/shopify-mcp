"""OrderQueryService — read-only access to the orders aggregate.

Thin service: takes a `UnitOfWork` factory, opens a UoW per call, dispatches
to the OrderRepository protocol. Does not import SQLAlchemy or any
concrete repository — the architecture tests in `tests/architecture/` enforce
that.

Limit handling: callers may request any positive `limit`, but it's clamped
to `MAX_LIMIT` server-side. This keeps a single bad client from asking for
millions of rows in one shot.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.domain.enums import FinancialStatus
from app.domain.models import (
    Customer,
    Order,
    OrderId,
    OrderLineItem,
    OrderLineRow,
    Page,
    Refund,
    StoreId,
)
from app.domain.repositories import UnitOfWork
from app.domain.specs import OrderSpec

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _clamp_limit(limit: int) -> int:
    return min(max(1, limit), MAX_LIMIT)


class OrderQueryService:
    """Read-only orders service. Wired by `app.container.Container`."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def list_orders(
        self,
        spec: OrderSpec,
        *,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> Page[Order]:
        with self._uow_factory() as uow:
            return uow.orders.find(spec, limit=_clamp_limit(limit), cursor=cursor)

    def get_order_by_id(self, order_id: OrderId) -> Order | None:
        with self._uow_factory() as uow:
            return uow.orders.get(order_id)

    def get_order_by_gid(self, store_id: StoreId, gid: str) -> Order | None:
        with self._uow_factory() as uow:
            return uow.orders.get_by_gid(store_id, gid)

    def list_refunds_for_order(self, order_id: OrderId) -> tuple[Refund, ...]:
        with self._uow_factory() as uow:
            return uow.refunds.list_for_order(order_id)

    def list_order_line_rows(
        self,
        spec: OrderSpec,
        *,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> Page[OrderLineRow]:
        """Return one denormalized row per line item across the matched orders.

        Pagination is order-grained — `limit` is the max number of orders
        per page (not rows). One page typically yields ~limit-to-5×limit
        rows depending on basket size. `next_cursor` is the same opaque
        token `OrderRepository.find` returns and can be passed back as
        `cursor=` on the next call.

        Customers are resolved once per page via per-id lookups inside
        the same UnitOfWork (no N+1 across connections). Orders with no
        line items emit a single row with line-item fields = None.
        """
        with self._uow_factory() as uow:
            order_page = uow.orders.find(spec, limit=_clamp_limit(limit), cursor=cursor)
            customer_ids = {o.customer_id for o in order_page.items if o.customer_id is not None}
            customers = {cid: uow.customers.get(cid) for cid in customer_ids}

        rows: list[OrderLineRow] = []
        for order in order_page.items:
            customer = customers.get(order.customer_id) if order.customer_id else None
            if not order.line_items:
                rows.append(_build_row(order, line_item=None, customer=customer))
                continue
            for line_item in order.line_items:
                rows.append(_build_row(order, line_item=line_item, customer=customer))

        return Page(items=tuple(rows), next_cursor=order_page.next_cursor)

    def count_orders_by_status(
        self,
        store_id: StoreId,
        since: datetime,
        until: datetime,
    ) -> dict[FinancialStatus, int]:
        with self._uow_factory() as uow:
            return uow.orders.count_by_status(store_id, since, until)


def _build_row(
    order: Order,
    *,
    line_item: OrderLineItem | None,
    customer: Customer | None,
) -> OrderLineRow:
    ship = order.shipping_address
    customer_email = (customer.email if customer is not None else None) or order.email
    line_extended = (
        (line_item.price * line_item.quantity) - line_item.total_discount
        if line_item is not None
        else None
    )
    return OrderLineRow(
        # Order context
        order_id=order.id,
        order_name=order.name,
        order_number=order.order_number,
        order_gid=order.gid,
        store_id=order.store_id,
        processed_at=order.processed_at,
        created_at=order.created_at,
        financial_status=order.financial_status,
        fulfillment_status=order.fulfillment_status,
        currency_code=order.currency_code,
        order_subtotal=order.subtotal_price,
        order_total=order.total_price,
        order_total_tax=order.total_tax,
        order_total_discounts=order.total_discounts,
        order_total_shipping=order.total_shipping,
        source_name=order.source_name,
        # Line item
        line_item_id=line_item.id if line_item else None,
        line_item_gid=line_item.gid if line_item else None,
        line_item_title=line_item.title if line_item else None,
        sku=line_item.sku if line_item else None,
        vendor=line_item.vendor if line_item else None,
        variant_id=line_item.variant_id if line_item else None,
        product_id=line_item.product_id if line_item else None,
        quantity=line_item.quantity if line_item else None,
        unit_price=line_item.price if line_item else None,
        line_total_discount=line_item.total_discount if line_item else None,
        line_extended=line_extended,
        line_fulfillment_status=line_item.fulfillment_status if line_item else None,
        requires_shipping=line_item.requires_shipping if line_item else None,
        taxable=line_item.taxable if line_item else None,
        # Shipping
        ship_name=ship.name if ship else None,
        ship_company=ship.company if ship else None,
        ship_address1=ship.address1 if ship else None,
        ship_address2=ship.address2 if ship else None,
        ship_city=ship.city if ship else None,
        ship_province=ship.province if ship else None,
        ship_country=ship.country if ship else None,
        ship_zip=ship.zip if ship else None,
        ship_phone=ship.phone if ship else None,
        ship_latitude=ship.latitude if ship else None,
        ship_longitude=ship.longitude if ship else None,
        # Customer
        customer_id=order.customer_id,
        customer_email=customer_email,
        customer_first_name=customer.first_name if customer else None,
        customer_last_name=customer.last_name if customer else None,
        customer_phone=customer.phone if customer else None,
    )
