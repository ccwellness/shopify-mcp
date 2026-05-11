"""Unit tests for `OrderRepository.aggregate_in_window`.

The in-memory fake mirrors the SQL impl's semantics (half-open window,
paid-only revenue + units, dominant-currency rule), so exercising it
verifies the contract that both repos honor.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.enums import (
    FinancialStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
)
from app.domain.models import (
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    StoreId,
)
from tests.fakes import InMemoryUnitOfWork

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)

T0 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

# Test fixture counts — named so PLR2004 doesn't complain about magic numbers.
THREE_ORDERS = 3
TWO_ORDERS = 2
FIVE_UNITS = 5  # 2 + 3 from a paid order's two line items


def _line_item(
    *,
    id: int,  # noqa: A002
    order_id: int,
    store_id: StoreId,
    quantity: int = 1,
) -> OrderLineItem:
    return OrderLineItem(
        id=OrderLineItemId(id),
        order_id=OrderId(order_id),
        store_id=store_id,
        variant_id=None,
        product_id=None,
        gid=None,
        legacy_id=None,
        title="Widget",
        sku="SKU-1",
        vendor=None,
        quantity=quantity,
        price=Decimal("9.99"),
        total_discount=Decimal("0.00"),
        fulfillment_status=OrderLineFulfillmentStatus.FULFILLED,
        requires_shipping=True,
        taxable=True,
    )


def _order(  # noqa: PLR0913 — test builder; explicit kwargs are clearer than a config dict
    *,
    id: int,  # noqa: A002
    store_id: StoreId = LUBELIFE,
    processed_at: datetime = T0,
    financial_status: FinancialStatus | None = FinancialStatus.PAID,
    total_price: Decimal = Decimal("100.00"),
    currency_code: str = "USD",
    line_quantities: tuple[int, ...] = (1,),
) -> Order:
    line_items = tuple(
        _line_item(id=10_000 + i, order_id=id, store_id=store_id, quantity=q)
        for i, q in enumerate(line_quantities)
    )
    return Order(
        id=OrderId(id),
        store_id=store_id,
        customer_id=None,
        gid=f"gid://shopify/Order/{id}",
        legacy_id=id,
        name=f"#TEST-{id}",
        order_number=id,
        email=None,
        financial_status=financial_status,
        fulfillment_status=FulfillmentStatus.FULFILLED,
        currency_code=currency_code,
        presentment_currency_code=None,
        subtotal_price=total_price - Decimal("2.00"),
        total_price=total_price,
        total_tax=Decimal("1.00"),
        total_discounts=Decimal("0.00"),
        total_shipping=Decimal("1.00"),
        presentment_subtotal_price=None,
        presentment_total_price=None,
        processed_at=processed_at,
        cancelled_at=None,
        closed_at=None,
        created_at=processed_at,
        updated_at=processed_at,
        line_items=line_items,
    )


@pytest.fixture
def uow(fake_uow: InMemoryUnitOfWork) -> InMemoryUnitOfWork:
    return fake_uow


def _aggregate(
    uow: InMemoryUnitOfWork,
    store_id: StoreId = LUBELIFE,
    *,
    since: datetime = T0 - timedelta(days=1),
    until: datetime = T0 + timedelta(days=10),
):
    with uow as u:
        return u.orders.aggregate_in_window(store_id, since, until)


def test_empty_window_returns_zeroed_aggregate(uow: InMemoryUnitOfWork) -> None:
    agg = _aggregate(uow)
    assert agg.count == 0
    assert agg.revenue == Decimal("0.00")
    assert agg.units == 0
    assert agg.currency_code is None
    assert agg.status_counts == {}


def test_aggregate_counts_all_statuses_but_only_paid_contributes_revenue(
    uow: InMemoryUnitOfWork,
) -> None:
    with uow as u:
        u.orders.upsert(_order(id=1, total_price=Decimal("100.00")))
        u.orders.upsert(
            _order(
                id=2,
                processed_at=T0 + timedelta(hours=1),
                financial_status=FinancialStatus.REFUNDED,
                total_price=Decimal("999.00"),
            )
        )
        u.orders.upsert(
            _order(
                id=3,
                processed_at=T0 + timedelta(hours=2),
                financial_status=FinancialStatus.PENDING,
                total_price=Decimal("50.00"),
            )
        )

    agg = _aggregate(uow)

    # All three orders contribute to count + status_counts.
    assert agg.count == THREE_ORDERS
    assert agg.status_counts == {
        FinancialStatus.PAID: 1,
        FinancialStatus.REFUNDED: 1,
        FinancialStatus.PENDING: 1,
    }
    # Only the paid order's $100 contributes to revenue.
    assert agg.revenue == Decimal("100.00")


def test_aggregate_units_only_paid_orders(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        u.orders.upsert(_order(id=1, line_quantities=(2, 3)))
        u.orders.upsert(
            _order(
                id=2,
                processed_at=T0 + timedelta(hours=1),
                financial_status=FinancialStatus.REFUNDED,
                line_quantities=(10,),
            )
        )

    agg = _aggregate(uow)

    # Only the paid order's 2 + 3 = 5 units count.
    assert agg.units == FIVE_UNITS


def test_aggregate_window_is_half_open_lower_inclusive_upper_exclusive(
    uow: InMemoryUnitOfWork,
) -> None:
    with uow as u:
        # Exactly at `since` — included.
        u.orders.upsert(_order(id=1, processed_at=T0))
        # Exactly at `until` — excluded.
        u.orders.upsert(_order(id=2, processed_at=T0 + timedelta(days=1)))
        # In window — included.
        u.orders.upsert(_order(id=3, processed_at=T0 + timedelta(hours=12)))

    with uow as u:
        agg = u.orders.aggregate_in_window(LUBELIFE, since=T0, until=T0 + timedelta(days=1))

    assert agg.count == TWO_ORDERS
    assert agg.status_counts == {FinancialStatus.PAID: TWO_ORDERS}


def test_aggregate_isolates_by_store(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        u.orders.upsert(_order(id=1, store_id=LUBELIFE, total_price=Decimal("100.00")))
        u.orders.upsert(
            _order(
                id=2,
                store_id=SHOPJO,
                processed_at=T0 + timedelta(hours=1),
                total_price=Decimal("9999.00"),
            )
        )

    agg = _aggregate(uow, store_id=LUBELIFE)
    assert agg.count == 1
    assert agg.revenue == Decimal("100.00")


def test_aggregate_picks_dominant_currency(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        u.orders.upsert(_order(id=1, currency_code="USD"))
        u.orders.upsert(_order(id=2, processed_at=T0 + timedelta(hours=1), currency_code="USD"))
        u.orders.upsert(_order(id=3, processed_at=T0 + timedelta(hours=2), currency_code="CAD"))

    agg = _aggregate(uow)
    assert agg.currency_code == "USD"


def test_aggregate_status_counts_excludes_none_status(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        u.orders.upsert(_order(id=1))
        u.orders.upsert(_order(id=2, processed_at=T0 + timedelta(hours=1), financial_status=None))

    agg = _aggregate(uow)
    # Count includes the None-status order; status_counts does not.
    assert agg.count == TWO_ORDERS
    assert agg.status_counts == {FinancialStatus.PAID: 1}
