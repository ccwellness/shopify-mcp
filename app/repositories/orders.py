"""SqlAlchemyOrderRepository — concrete `OrderRepository`.

Order is an aggregate (Order + LineItem + ShippingAddress + Fulfillment).
Children eager-load via `lazy='selectin'` on the ORM side, and on upsert
the children are wholesale replaced — v1's reconcile-on-write pattern.

Pagination on `find` uses keyset cursor on (processed_at, id) DESC.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.orm import Session

from app.db.orm.customer import CustomerRow
from app.db.orm.order import (
    FulfillmentRow,
    OrderLineItemRow,
    OrderRow,
    OrderShippingAddressRow,
)
from app.domain.enums import (
    FinancialStatus,
    FulfillmentExecutionStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
    ShipmentStatus,
)
from app.domain.models import (
    CustomerId,
    Fulfillment,
    FulfillmentId,
    LocationId,
    Money,
    Order,
    OrderAggregate,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    OrderShippingAddress,
    Page,
    ProductId,
    ProductSalesDay,
    StoreId,
    VariantId,
)
from app.domain.specs import OrderSpec
from app.repositories._cursor import decode, encode

# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------


def _line_row_to_domain(row: OrderLineItemRow) -> OrderLineItem:
    return OrderLineItem(
        id=OrderLineItemId(row.id),
        order_id=OrderId(row.order_id),
        store_id=StoreId(row.store_id),
        variant_id=VariantId(row.variant_id) if row.variant_id is not None else None,
        product_id=ProductId(row.product_id) if row.product_id is not None else None,
        gid=row.gid,
        legacy_id=row.legacy_id,
        title=row.title,
        sku=row.sku,
        vendor=row.vendor,
        quantity=row.quantity,
        price=row.price,
        total_discount=row.total_discount,
        fulfillment_status=(
            OrderLineFulfillmentStatus(row.fulfillment_status) if row.fulfillment_status else None
        ),
        requires_shipping=row.requires_shipping,
        taxable=row.taxable,
    )


def _addr_row_to_domain(row: OrderShippingAddressRow) -> OrderShippingAddress:
    return OrderShippingAddress(
        order_id=OrderId(row.order_id),
        store_id=StoreId(row.store_id),
        name=row.name,
        company=row.company,
        address1=row.address1,
        address2=row.address2,
        city=row.city,
        province=row.province,
        country=row.country,
        zip=row.zip,
        phone=row.phone,
        latitude=row.latitude,
        longitude=row.longitude,
    )


def _fulfillment_row_to_domain(row: FulfillmentRow) -> Fulfillment:
    return Fulfillment(
        id=FulfillmentId(row.id),
        order_id=OrderId(row.order_id),
        store_id=StoreId(row.store_id),
        location_id=LocationId(row.location_id) if row.location_id is not None else None,
        gid=row.gid,
        legacy_id=row.legacy_id,
        status=FulfillmentExecutionStatus(row.status),
        shipment_status=ShipmentStatus(row.shipment_status) if row.shipment_status else None,
        tracking_company=row.tracking_company,
        tracking_number=row.tracking_number,
        tracking_url=row.tracking_url,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_domain(row: OrderRow) -> Order:
    return Order(
        id=OrderId(row.id),
        store_id=StoreId(row.store_id),
        customer_id=CustomerId(row.customer_id) if row.customer_id is not None else None,
        gid=row.gid,
        legacy_id=row.legacy_id,
        name=row.name,
        order_number=row.order_number,
        email=row.email,
        financial_status=FinancialStatus(row.financial_status) if row.financial_status else None,
        fulfillment_status=(
            FulfillmentStatus(row.fulfillment_status) if row.fulfillment_status else None
        ),
        currency_code=row.currency_code,
        presentment_currency_code=row.presentment_currency_code,
        subtotal_price=row.subtotal_price,
        total_price=row.total_price,
        total_tax=row.total_tax,
        total_discounts=row.total_discounts,
        total_shipping=row.total_shipping,
        presentment_subtotal_price=row.presentment_subtotal_price,
        presentment_total_price=row.presentment_total_price,
        processed_at=row.processed_at,
        cancelled_at=row.cancelled_at,
        closed_at=row.closed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        line_items=tuple(_line_row_to_domain(li) for li in row.line_items),
        shipping_address=(
            _addr_row_to_domain(row.shipping_address) if row.shipping_address else None
        ),
        fulfillments=tuple(_fulfillment_row_to_domain(f) for f in row.fulfillments),
    )


def _line_to_row(line: OrderLineItem) -> OrderLineItemRow:
    return OrderLineItemRow(
        store_id=int(line.store_id),
        variant_id=int(line.variant_id) if line.variant_id is not None else None,
        product_id=int(line.product_id) if line.product_id is not None else None,
        gid=line.gid,
        legacy_id=line.legacy_id,
        title=line.title,
        sku=line.sku,
        vendor=line.vendor,
        quantity=line.quantity,
        price=line.price,
        total_discount=line.total_discount,
        fulfillment_status=line.fulfillment_status.value if line.fulfillment_status else None,
        requires_shipping=line.requires_shipping,
        taxable=line.taxable,
    )


def _addr_to_row(addr: OrderShippingAddress) -> OrderShippingAddressRow:
    return OrderShippingAddressRow(
        store_id=int(addr.store_id),
        name=addr.name,
        company=addr.company,
        address1=addr.address1,
        address2=addr.address2,
        city=addr.city,
        province=addr.province,
        country=addr.country,
        zip=addr.zip,
        phone=addr.phone,
        latitude=addr.latitude,
        longitude=addr.longitude,
    )


def _fulfillment_to_row(f: Fulfillment) -> FulfillmentRow:
    return FulfillmentRow(
        store_id=int(f.store_id),
        location_id=int(f.location_id) if f.location_id is not None else None,
        gid=f.gid,
        legacy_id=f.legacy_id,
        status=f.status.value,
        shipment_status=f.shipment_status.value if f.shipment_status else None,
        tracking_company=f.tracking_company,
        tracking_number=f.tracking_number,
        tracking_url=f.tracking_url,
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class SqlAlchemyOrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, order_id: OrderId) -> Order | None:
        row = self._session.get(OrderRow, int(order_id))
        return _row_to_domain(row) if row else None

    def get_by_gid(self, store_id: StoreId, gid: str) -> Order | None:
        row = self._session.scalar(
            select(OrderRow).where(
                OrderRow.store_id == int(store_id),
                OrderRow.gid == gid,
            )
        )
        return _row_to_domain(row) if row else None

    def find(
        self,
        spec: OrderSpec,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[Order]:
        stmt = select(OrderRow)
        if spec.store_ids is not None:
            stmt = stmt.where(OrderRow.store_id.in_([int(s) for s in spec.store_ids]))
        if spec.since is not None:
            stmt = stmt.where(OrderRow.processed_at >= spec.since)
        if spec.until is not None:
            stmt = stmt.where(OrderRow.processed_at < spec.until)
        if spec.financial_status is not None:
            stmt = stmt.where(OrderRow.financial_status == spec.financial_status.value)
        if spec.fulfillment_status is not None:
            stmt = stmt.where(OrderRow.fulfillment_status == spec.fulfillment_status.value)
        if spec.customer_id is not None:
            stmt = stmt.where(OrderRow.customer_id == int(spec.customer_id))
        if spec.customer_email:
            # email matches either order.email (guest) or the linked customer's email.
            stmt = stmt.outerjoin(CustomerRow, CustomerRow.id == OrderRow.customer_id).where(
                (OrderRow.email == spec.customer_email) | (CustomerRow.email == spec.customer_email)
            )
        if spec.sku:
            stmt = stmt.join(OrderLineItemRow, OrderLineItemRow.order_id == OrderRow.id).where(
                OrderLineItemRow.sku == spec.sku
            )
        if spec.min_total is not None:
            stmt = stmt.where(OrderRow.total_price >= spec.min_total)
        if cursor:
            cur_processed_at, cur_id = decode(cursor)
            stmt = stmt.where(
                tuple_(OrderRow.processed_at, OrderRow.id)
                < tuple_(literal(cur_processed_at), literal(cur_id))
            )
        stmt = stmt.order_by(OrderRow.processed_at.desc(), OrderRow.id.desc()).limit(limit + 1)
        rows = self._session.scalars(stmt).unique().all()
        items = [_row_to_domain(r) for r in rows[:limit]]
        next_cursor = (
            encode(rows[limit - 1].processed_at, rows[limit - 1].id) if len(rows) > limit else None
        )
        return Page(items=tuple(items), next_cursor=next_cursor)

    def count_by_status(
        self,
        store_id: StoreId,
        since: datetime,
        until: datetime,
    ) -> dict[FinancialStatus, int]:
        rows = self._session.execute(
            select(OrderRow.financial_status, func.count(OrderRow.id))
            .where(
                OrderRow.store_id == int(store_id),
                OrderRow.processed_at >= since,
                OrderRow.processed_at < until,
                OrderRow.financial_status.is_not(None),
            )
            .group_by(OrderRow.financial_status)
        ).all()
        return {FinancialStatus(status): count for status, count in rows if status is not None}

    def aggregate_in_window(
        self,
        store_id: StoreId,
        since: datetime,
        until: datetime,
    ) -> OrderAggregate:
        # Window predicate reused across the three queries below.
        in_window = (
            OrderRow.store_id == int(store_id),
            OrderRow.processed_at >= since,
            OrderRow.processed_at < until,
        )
        paid = OrderRow.financial_status == FinancialStatus.PAID.value

        # 1) status_counts + total count + paid revenue, in one GROUP BY.
        status_rows = self._session.execute(
            select(
                OrderRow.financial_status,
                func.count(OrderRow.id),
                func.coalesce(func.sum(OrderRow.total_price), Decimal("0")),
            )
            .where(*in_window)
            .group_by(OrderRow.financial_status)
        ).all()
        count = 0
        revenue = Decimal("0.00")
        status_counts: dict[FinancialStatus, int] = {}
        for status_value, n, rev in status_rows:
            count += int(n)
            if status_value is not None:
                try:
                    status_counts[FinancialStatus(status_value)] = int(n)
                except ValueError:
                    # Unknown status (forward-compat): drop into status_counts as-is would
                    # break the typed dict; just skip — the order still contributes to count.
                    pass
                if status_value == FinancialStatus.PAID.value:
                    revenue = Decimal(rev) if rev is not None else Decimal("0.00")

        # 2) units sold across paid orders only (line item quantities).
        units = self._session.scalar(
            select(func.coalesce(func.sum(OrderLineItemRow.quantity), 0))
            .select_from(OrderLineItemRow)
            .join(OrderRow, OrderLineItemRow.order_id == OrderRow.id)
            .where(*in_window, paid)
        )

        # 3) Dominant currency code for the matched orders (or None when empty).
        currency_row = self._session.execute(
            select(OrderRow.currency_code, func.count(OrderRow.id).label("n"))
            .where(*in_window)
            .group_by(OrderRow.currency_code)
            .order_by(func.count(OrderRow.id).desc())
            .limit(1)
        ).first()
        currency_code = currency_row[0] if currency_row is not None else None

        return OrderAggregate(
            store_id=store_id,
            since=since,
            until=until,
            count=count,
            revenue=revenue,
            units=int(units or 0),
            currency_code=currency_code,
            status_counts=status_counts,
        )

    def sales_by_day_for_product(
        self,
        store_id: StoreId,
        product_id: ProductId,
        since: datetime,
        until: datetime,
    ) -> tuple[ProductSalesDay, ...]:
        # Per-day rollup of units + gross revenue + distinct order count
        # for one product within the window. Refunds are not netted here;
        # the schema doesn't decompose them to line items.
        day_bucket = func.date_trunc("day", OrderRow.processed_at).label("day")
        gross = func.coalesce(
            func.sum(
                OrderLineItemRow.price * OrderLineItemRow.quantity
                - func.coalesce(OrderLineItemRow.total_discount, Decimal("0"))
            ),
            Decimal("0"),
        )
        rows = self._session.execute(
            select(
                day_bucket,
                func.coalesce(func.sum(OrderLineItemRow.quantity), 0),
                gross,
                func.count(func.distinct(OrderRow.id)),
            )
            .select_from(OrderLineItemRow)
            .join(OrderRow, OrderLineItemRow.order_id == OrderRow.id)
            .where(
                OrderLineItemRow.product_id == int(product_id),
                OrderRow.store_id == int(store_id),
                OrderRow.processed_at >= since,
                OrderRow.processed_at < until,
            )
            .group_by(day_bucket)
            .order_by(day_bucket)
        ).all()
        return tuple(
            ProductSalesDay(
                date=day.date() if hasattr(day, "date") else day,
                units=int(units or 0),
                gross_revenue=Money(revenue) if revenue is not None else Money("0"),
                order_count=int(orders or 0),
            )
            for day, units, revenue, orders in rows
        )

    def find_orders_containing_product(
        self,
        store_id: StoreId,
        product_id: ProductId,
        *,
        limit: int = 20,
    ) -> tuple[Order, ...]:
        # Two-step: pick the order ids first (DISTINCT + ORDER BY + LIMIT
        # in one query), then load the full aggregates. Keeps the keyset
        # logic and the eager-load path separate.
        id_rows = self._session.execute(
            select(OrderRow.id, OrderRow.processed_at)
            .join(OrderLineItemRow, OrderLineItemRow.order_id == OrderRow.id)
            .where(
                OrderLineItemRow.product_id == int(product_id),
                OrderRow.store_id == int(store_id),
            )
            .group_by(OrderRow.id, OrderRow.processed_at)
            .order_by(OrderRow.processed_at.desc(), OrderRow.id.desc())
            .limit(limit)
        ).all()
        order_ids = [oid for oid, _ in id_rows]
        if not order_ids:
            return ()
        rows = (
            self._session.scalars(select(OrderRow).where(OrderRow.id.in_(order_ids))).unique().all()
        )
        # Restore the (processed_at desc, id desc) order — the IN clause
        # doesn't preserve it.
        by_id = {r.id: r for r in rows}
        return tuple(_row_to_domain(by_id[oid]) for oid in order_ids if oid in by_id)

    def upsert(self, order: Order) -> None:
        existing = self._session.scalar(
            select(OrderRow).where(
                OrderRow.store_id == int(order.store_id),
                OrderRow.gid == order.gid,
            )
        )
        if existing is None:
            row = OrderRow(
                store_id=int(order.store_id),
                customer_id=int(order.customer_id) if order.customer_id is not None else None,
                gid=order.gid,
                legacy_id=order.legacy_id,
                name=order.name,
                order_number=order.order_number,
                email=order.email,
                financial_status=order.financial_status.value if order.financial_status else None,
                fulfillment_status=(
                    order.fulfillment_status.value if order.fulfillment_status else None
                ),
                currency_code=order.currency_code,
                presentment_currency_code=order.presentment_currency_code,
                subtotal_price=order.subtotal_price,
                total_price=order.total_price,
                total_tax=order.total_tax,
                total_discounts=order.total_discounts,
                total_shipping=order.total_shipping,
                presentment_subtotal_price=order.presentment_subtotal_price,
                presentment_total_price=order.presentment_total_price,
                processed_at=order.processed_at,
                cancelled_at=order.cancelled_at,
                closed_at=order.closed_at,
            )
            for li in order.line_items:
                row.line_items.append(_line_to_row(li))
            if order.shipping_address is not None:
                row.shipping_address = _addr_to_row(order.shipping_address)
            for f in order.fulfillments:
                row.fulfillments.append(_fulfillment_to_row(f))
            self._session.add(row)
        else:
            existing.customer_id = int(order.customer_id) if order.customer_id is not None else None
            existing.legacy_id = order.legacy_id
            existing.name = order.name
            existing.order_number = order.order_number
            existing.email = order.email
            existing.financial_status = (
                order.financial_status.value if order.financial_status else None
            )
            existing.fulfillment_status = (
                order.fulfillment_status.value if order.fulfillment_status else None
            )
            existing.currency_code = order.currency_code
            existing.presentment_currency_code = order.presentment_currency_code
            existing.subtotal_price = order.subtotal_price
            existing.total_price = order.total_price
            existing.total_tax = order.total_tax
            existing.total_discounts = order.total_discounts
            existing.total_shipping = order.total_shipping
            existing.presentment_subtotal_price = order.presentment_subtotal_price
            existing.presentment_total_price = order.presentment_total_price
            existing.processed_at = order.processed_at
            existing.cancelled_at = order.cancelled_at
            existing.closed_at = order.closed_at
            # Replace child collections wholesale (v1 reconcile-on-write).
            # .clear() / = None on delete-orphan relationships triggers proper
            # cascading deletes — raw DELETE leaves stale in-memory references.
            existing.line_items.clear()
            existing.fulfillments.clear()
            existing.shipping_address = None
            self._session.flush()
            for li in order.line_items:
                existing.line_items.append(_line_to_row(li))
            if order.shipping_address is not None:
                existing.shipping_address = _addr_to_row(order.shipping_address)
            for f in order.fulfillments:
                existing.fulfillments.append(_fulfillment_to_row(f))
        self._session.flush()
