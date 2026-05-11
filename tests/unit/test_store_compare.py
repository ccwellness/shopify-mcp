"""Unit tests for StoreComparisonService.

Drives the service against the in-memory persistence layer. Covers:
- empty case, single store, multi-store
- refund netting (paid_revenue − refunds_total → net_revenue)
- currency_warning behavior + total_net_revenue guarding
- store_ids filter
- since/until validation
- stable ordering by store_key
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.enums import (
    FinancialStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
    SubscriptionProvider,
)
from app.domain.models import (
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    Refund,
    RefundId,
    Store,
    StoreId,
)
from app.domain.repositories import UnitOfWork
from app.services.store_compare import StoreComparisonService

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)
SHOPSHIBARI = StoreId(3)

T0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
WINDOW_END = T0 + timedelta(days=7)

EXPECTED_TWO_STORES = 2
EXPECTED_THREE_STORES = 3


def _store(*, sid: StoreId, key: str, currency: str = "USD") -> Store:
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
        currency_code=currency,
        created_at=T0,
        updated_at=T0,
    )


def _line_item(order_id: int, store_id: StoreId, *, quantity: int = 1) -> OrderLineItem:
    return OrderLineItem(
        id=OrderLineItemId(10_000 + order_id),
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


def _order(  # noqa: PLR0913 — test builder; explicit kwargs beat a config dict
    *,
    id: int,  # noqa: A002
    store_id: StoreId,
    processed_at: datetime = T0,
    financial_status: FinancialStatus | None = FinancialStatus.PAID,
    total_price: Decimal = Decimal("100.00"),
    currency_code: str = "USD",
    quantity: int = 1,
) -> Order:
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
        line_items=(_line_item(id, store_id, quantity=quantity),),
    )


def _refund(
    *,
    rid: int,
    store_id: StoreId,
    order_id: int,
    amount: Decimal,
    created_at: datetime = T0,
) -> Refund:
    return Refund(
        id=RefundId(0),  # repo assigns
        store_id=store_id,
        order_id=OrderId(order_id),
        gid=f"gid://shopify/Refund/{rid}",
        legacy_id=rid,
        amount=amount,
        currency_code="USD",
        note=None,
        created_at=created_at,
    )


@pytest.fixture
def service(fake_uow_factory: Callable[[], UnitOfWork]) -> StoreComparisonService:
    return StoreComparisonService(fake_uow_factory)


@pytest.fixture
def seeded_stores(fake_uow: UnitOfWork) -> UnitOfWork:
    """Seed two active stores. Per-test additions stack on top of this."""
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.stores.upsert(_store(sid=SHOPJO, key="shopjo"))
    return fake_uow


# ---------------------------------------------------------------------------
# Empty / no-data cases
# ---------------------------------------------------------------------------


def test_compare_orders_with_no_stores_returns_empty_rows(
    service: StoreComparisonService,
) -> None:
    out = service.compare_orders(since=T0, until=WINDOW_END)
    assert out.rows == ()
    assert out.currency_warning is False


def test_compare_orders_seeded_stores_with_no_orders_returns_zeroed_rows(
    service: StoreComparisonService, seeded_stores: UnitOfWork
) -> None:
    out = service.compare_orders(since=T0, until=WINDOW_END)
    assert len(out.rows) == EXPECTED_TWO_STORES
    for row in out.rows:
        assert row.order_count == 0
        assert row.paid_revenue == Decimal("0.00")
        assert row.refunds_total == Decimal("0")
        assert row.net_revenue == Decimal("0.00")
        assert row.units_sold == 0


# ---------------------------------------------------------------------------
# Happy path — orders + refund netting
# ---------------------------------------------------------------------------


def test_compare_orders_per_store_rollup_and_net_revenue(
    service: StoreComparisonService, seeded_stores: UnitOfWork
) -> None:
    with seeded_stores as uow:
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, total_price=Decimal("100.00")))
        uow.orders.upsert(
            _order(
                id=2,
                store_id=LUBELIFE,
                processed_at=T0 + timedelta(hours=1),
                total_price=Decimal("50.00"),
            )
        )
        uow.orders.upsert(_order(id=3, store_id=SHOPJO, total_price=Decimal("200.00"), quantity=3))
        uow.refunds.upsert(_refund(rid=10, store_id=LUBELIFE, order_id=1, amount=Decimal("20.00")))
        uow.refunds.upsert(_refund(rid=11, store_id=SHOPJO, order_id=3, amount=Decimal("50.00")))

    out = service.compare_orders(since=T0, until=WINDOW_END)
    by_key = {r.store_key: r for r in out.rows}

    assert by_key["lubelife"].order_count == EXPECTED_TWO_STORES
    assert by_key["lubelife"].paid_revenue == Decimal("150.00")
    assert by_key["lubelife"].refunds_total == Decimal("20.00")
    assert by_key["lubelife"].net_revenue == Decimal("130.00")
    assert by_key["lubelife"].units_sold == EXPECTED_TWO_STORES

    assert by_key["shopjo"].order_count == 1
    assert by_key["shopjo"].paid_revenue == Decimal("200.00")
    assert by_key["shopjo"].refunds_total == Decimal("50.00")
    assert by_key["shopjo"].net_revenue == Decimal("150.00")
    assert by_key["shopjo"].units_sold == EXPECTED_THREE_STORES


def test_compare_orders_refunds_outside_window_do_not_deduct(
    service: StoreComparisonService, seeded_stores: UnitOfWork
) -> None:
    with seeded_stores as uow:
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, total_price=Decimal("100.00")))
        # Refund a week after the window ends — must not deduct from this window.
        uow.refunds.upsert(
            _refund(
                rid=10,
                store_id=LUBELIFE,
                order_id=1,
                amount=Decimal("75.00"),
                created_at=WINDOW_END + timedelta(days=7),
            )
        )

    out = service.compare_orders(since=T0, until=WINDOW_END)
    lubelife = next(r for r in out.rows if r.store_key == "lubelife")
    assert lubelife.refunds_total == Decimal("0")
    assert lubelife.net_revenue == Decimal("100.00")


def test_compare_orders_only_paid_orders_contribute_to_revenue(
    service: StoreComparisonService, seeded_stores: UnitOfWork
) -> None:
    with seeded_stores as uow:
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, total_price=Decimal("100.00")))
        uow.orders.upsert(
            _order(
                id=2,
                store_id=LUBELIFE,
                processed_at=T0 + timedelta(hours=1),
                financial_status=FinancialStatus.PENDING,
                total_price=Decimal("999.00"),
            )
        )

    out = service.compare_orders(since=T0, until=WINDOW_END)
    lubelife = next(r for r in out.rows if r.store_key == "lubelife")
    # Pending order is counted in order_count + status_counts but not revenue/units.
    assert lubelife.order_count == EXPECTED_TWO_STORES
    assert lubelife.paid_revenue == Decimal("100.00")
    assert lubelife.status_counts[FinancialStatus.PENDING] == 1


# ---------------------------------------------------------------------------
# Currency handling
# ---------------------------------------------------------------------------


def test_compare_orders_flags_currency_mismatch(
    service: StoreComparisonService, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife", currency="USD"))
        uow.stores.upsert(_store(sid=SHOPJO, key="shopjo", currency="CAD"))
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, currency_code="USD"))
        uow.orders.upsert(
            _order(id=2, store_id=SHOPJO, currency_code="CAD", total_price=Decimal("50.00"))
        )

    out = service.compare_orders(since=T0, until=WINDOW_END)
    assert out.currency_warning is True


def test_compare_orders_no_currency_warning_when_single_currency(
    service: StoreComparisonService, seeded_stores: UnitOfWork
) -> None:
    with seeded_stores as uow:
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, currency_code="USD"))
        uow.orders.upsert(_order(id=2, store_id=SHOPJO, currency_code="USD"))

    out = service.compare_orders(since=T0, until=WINDOW_END)
    assert out.currency_warning is False


def test_total_net_revenue_sums_rows_in_single_currency(
    service: StoreComparisonService, seeded_stores: UnitOfWork
) -> None:
    with seeded_stores as uow:
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, total_price=Decimal("100.00")))
        uow.orders.upsert(_order(id=2, store_id=SHOPJO, total_price=Decimal("200.00")))
        uow.refunds.upsert(_refund(rid=1, store_id=LUBELIFE, order_id=1, amount=Decimal("30.00")))

    out = service.compare_orders(since=T0, until=WINDOW_END)
    assert service.total_net_revenue(out) == Decimal("270.00")


def test_total_net_revenue_raises_when_currencies_differ(
    service: StoreComparisonService, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife", currency="USD"))
        uow.stores.upsert(_store(sid=SHOPJO, key="shopjo", currency="CAD"))
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, currency_code="USD"))
        uow.orders.upsert(_order(id=2, store_id=SHOPJO, currency_code="CAD"))

    out = service.compare_orders(since=T0, until=WINDOW_END)
    with pytest.raises(ValueError, match="mixed currencies"):
        service.total_net_revenue(out)


# ---------------------------------------------------------------------------
# Filtering + ordering + validation
# ---------------------------------------------------------------------------


def test_compare_orders_filters_by_store_ids(
    service: StoreComparisonService, seeded_stores: UnitOfWork
) -> None:
    out = service.compare_orders(since=T0, until=WINDOW_END, store_ids=(SHOPJO,))
    assert len(out.rows) == 1
    assert out.rows[0].store_key == "shopjo"


def test_compare_orders_unknown_store_ids_silently_dropped(
    service: StoreComparisonService, seeded_stores: UnitOfWork
) -> None:
    out = service.compare_orders(since=T0, until=WINDOW_END, store_ids=(StoreId(999),))
    assert out.rows == ()


def test_compare_orders_rows_ordered_by_store_key(
    service: StoreComparisonService, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.stores.upsert(_store(sid=SHOPSHIBARI, key="shopshibari"))
        uow.stores.upsert(_store(sid=LUBELIFE, key="lubelife"))
        uow.stores.upsert(_store(sid=SHOPJO, key="shopjo"))

    out = service.compare_orders(since=T0, until=WINDOW_END)
    assert tuple(r.store_key for r in out.rows) == ("lubelife", "shopjo", "shopshibari")


def test_compare_orders_rejects_inverted_window(service: StoreComparisonService) -> None:
    with pytest.raises(ValueError, match="strictly before"):
        service.compare_orders(since=WINDOW_END, until=T0)


def test_compare_orders_rejects_equal_window(service: StoreComparisonService) -> None:
    with pytest.raises(ValueError, match="strictly before"):
        service.compare_orders(since=T0, until=T0)
