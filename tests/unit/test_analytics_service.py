"""Unit tests for `AnalyticsService` — the KPI rollup that folds
`sessions_daily` + paid-orders aggregate into `analytics_kpi_daily`.

Exercised against the in-memory persistence layer so the same fakes
that prove protocol conformance also prove the rollup math.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.enums import (
    AnalyticsSource,
    FinancialStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
)
from app.domain.models import (
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    SessionsDay,
    StoreId,
)
from app.domain.repositories import UnitOfWork
from app.services.analytics import AnalyticsService

LUBELIFE = StoreId(1)
DAY = date(2026, 5, 10)
DAY_START = datetime(2026, 5, 10, 0, 0, tzinfo=UTC)

EXPECTED_TWO_DAYS = 2
EXPECTED_THREE_DAYS = 3


def _line_item(order_id: int, store_id: StoreId, *, quantity: int = 1) -> OrderLineItem:
    return OrderLineItem(
        id=OrderLineItemId(10_000 + order_id),
        order_id=OrderId(order_id),
        store_id=store_id,
        variant_id=None,
        product_id=None,
        gid=None,
        legacy_id=None,
        title="W",
        sku="SKU-1",
        vendor=None,
        quantity=quantity,
        price=Decimal("9.99"),
        total_discount=Decimal("0.00"),
        fulfillment_status=OrderLineFulfillmentStatus.FULFILLED,
        requires_shipping=True,
        taxable=True,
    )


def _order(
    *,
    id: int,  # noqa: A002
    processed_at: datetime,
    total: Decimal = Decimal("100.00"),
    financial_status: FinancialStatus | None = FinancialStatus.PAID,
    quantity: int = 1,
) -> Order:
    return Order(
        id=OrderId(id),
        store_id=LUBELIFE,
        customer_id=None,
        gid=f"gid://shopify/Order/{id}",
        legacy_id=id,
        name=f"#TEST-{id}",
        order_number=id,
        email=None,
        financial_status=financial_status,
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
        processed_at=processed_at,
        cancelled_at=None,
        closed_at=None,
        created_at=processed_at,
        updated_at=processed_at,
        line_items=(_line_item(id, LUBELIFE, quantity=quantity),),
    )


def _sessions(
    *, day: date, sessions: int | None = 1000, orders: int = 25, total_sales: Decimal | None = None
) -> SessionsDay:
    return SessionsDay(
        store_id=LUBELIFE,
        date=day,
        sessions=sessions,
        orders=orders,
        total_sales=total_sales,
        units_sold=None,
        source=AnalyticsSource.SHOPIFYQL,
        pulled_at=datetime(2026, 5, 12, tzinfo=UTC),
    )


@pytest.fixture
def service(fake_uow_factory: Callable[[], UnitOfWork]) -> AnalyticsService:
    return AnalyticsService(fake_uow_factory)


# ---------------------------------------------------------------------------
# compute_kpi_day
# ---------------------------------------------------------------------------


def test_compute_kpi_day_returns_none_when_sessions_missing(
    service: AnalyticsService,
) -> None:
    # No sessions_daily row → service refuses to fabricate one.
    assert service.compute_kpi_day(LUBELIFE, DAY) is None


def test_compute_kpi_day_with_sessions_and_no_orders_yields_zero_orders(
    service: AnalyticsService, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.analytics.upsert_sessions_day(_sessions(day=DAY, sessions=500))

    row = service.compute_kpi_day(LUBELIFE, DAY)
    assert row is not None
    assert row.sessions == 500  # noqa: PLR2004
    assert row.orders == 0
    assert row.units == 0
    assert row.revenue == Decimal("0.0000")
    # 0 / 500 → 0.0000
    assert row.conversion_rate == Decimal("0.0000")
    # 0 orders → AOV undefined
    assert row.aov is None


def test_compute_kpi_day_combines_sessions_and_paid_orders(
    service: AnalyticsService, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.analytics.upsert_sessions_day(_sessions(day=DAY, sessions=1000))
        # 4 paid orders on May 10: $100, $50, $200, $150 → revenue $500
        # quantities: 1, 1, 3, 2 → units 7
        uow.orders.upsert(
            _order(id=1, processed_at=DAY_START + timedelta(hours=1), total=Decimal("100.00"))
        )
        uow.orders.upsert(
            _order(id=2, processed_at=DAY_START + timedelta(hours=2), total=Decimal("50.00"))
        )
        uow.orders.upsert(
            _order(
                id=3,
                processed_at=DAY_START + timedelta(hours=3),
                total=Decimal("200.00"),
                quantity=3,
            )
        )
        uow.orders.upsert(
            _order(
                id=4,
                processed_at=DAY_START + timedelta(hours=4),
                total=Decimal("150.00"),
                quantity=2,
            )
        )

    row = service.compute_kpi_day(LUBELIFE, DAY)
    assert row is not None
    expected_orders = 4
    expected_units = 7
    assert row.sessions == 1000  # noqa: PLR2004
    assert row.orders == expected_orders
    assert row.units == expected_units
    assert row.revenue == Decimal("500.0000")
    # 4 / 1000 = 0.0040
    assert row.conversion_rate == Decimal("0.0040")
    # 500 / 4 = 125.00
    assert row.aov == Decimal("125.0000")


def test_compute_kpi_day_excludes_orders_outside_the_day(
    service: AnalyticsService, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.analytics.upsert_sessions_day(_sessions(day=DAY, sessions=1000))
        # Order 1: late on May 9 (excluded). Order 2: early on May 10 (included).
        uow.orders.upsert(
            _order(id=1, processed_at=DAY_START - timedelta(hours=1), total=Decimal("999.00"))
        )
        uow.orders.upsert(
            _order(id=2, processed_at=DAY_START + timedelta(minutes=1), total=Decimal("50.00"))
        )

    row = service.compute_kpi_day(LUBELIFE, DAY)
    assert row is not None
    assert row.orders == 1
    assert row.revenue == Decimal("50.0000")


def test_compute_kpi_day_zero_sessions_yields_none_conversion(
    service: AnalyticsService, fake_uow: UnitOfWork
) -> None:
    with fake_uow as uow:
        uow.analytics.upsert_sessions_day(_sessions(day=DAY, sessions=0))
        uow.orders.upsert(
            _order(id=1, processed_at=DAY_START + timedelta(hours=1), total=Decimal("100.00"))
        )

    row = service.compute_kpi_day(LUBELIFE, DAY)
    assert row is not None
    assert row.sessions == 0
    assert row.orders == 1
    # 1 / 0 is undefined → service returns None instead of raising.
    assert row.conversion_rate is None
    # AOV still computed because orders > 0.
    assert row.aov == Decimal("100.0000")


def test_compute_kpi_day_none_sessions_yields_none_conversion(
    service: AnalyticsService, fake_uow: UnitOfWork
) -> None:
    # ShopifyQL returned a row but sessions column was suppressed.
    with fake_uow as uow:
        uow.analytics.upsert_sessions_day(_sessions(day=DAY, sessions=None))
        uow.orders.upsert(
            _order(id=1, processed_at=DAY_START + timedelta(hours=1), total=Decimal("100.00"))
        )

    row = service.compute_kpi_day(LUBELIFE, DAY)
    assert row is not None
    assert row.sessions is None
    assert row.conversion_rate is None


def test_compute_kpi_day_quantizes_conversion_to_4_decimals(
    service: AnalyticsService, fake_uow: UnitOfWork
) -> None:
    # 1 / 3 = 0.3333... — must quantize to Numeric(7,4) without truncating to 0.
    with fake_uow as uow:
        uow.analytics.upsert_sessions_day(_sessions(day=DAY, sessions=3))
        uow.orders.upsert(
            _order(id=1, processed_at=DAY_START + timedelta(hours=1), total=Decimal("99.00"))
        )

    row = service.compute_kpi_day(LUBELIFE, DAY)
    assert row is not None
    assert row.conversion_rate == Decimal("0.3333")


def test_compute_kpi_day_persists_row(service: AnalyticsService, fake_uow: UnitOfWork) -> None:
    with fake_uow as uow:
        uow.analytics.upsert_sessions_day(_sessions(day=DAY, sessions=100))

    service.compute_kpi_day(LUBELIFE, DAY)

    with fake_uow as uow:
        stored = uow.analytics.get_kpi_day(LUBELIFE, DAY)
    assert stored is not None
    assert stored.sessions == 100  # noqa: PLR2004


# ---------------------------------------------------------------------------
# compute_kpi_window
# ---------------------------------------------------------------------------


def test_compute_kpi_window_walks_each_day(service: AnalyticsService, fake_uow: UnitOfWork) -> None:
    with fake_uow as uow:
        # Seed sessions for May 8 + May 10 (skip May 9).
        uow.analytics.upsert_sessions_day(_sessions(day=date(2026, 5, 8), sessions=100))
        uow.analytics.upsert_sessions_day(_sessions(day=date(2026, 5, 10), sessions=200))

    result = service.compute_kpi_window(LUBELIFE, since=date(2026, 5, 8), until=date(2026, 5, 10))
    assert result.days_computed == EXPECTED_TWO_DAYS
    assert result.days_skipped_no_sessions == 1
    # Verify the two rows landed and the May 9 gap stayed empty.
    with fake_uow as uow:
        assert uow.analytics.get_kpi_day(LUBELIFE, date(2026, 5, 8)) is not None
        assert uow.analytics.get_kpi_day(LUBELIFE, date(2026, 5, 9)) is None
        assert uow.analytics.get_kpi_day(LUBELIFE, date(2026, 5, 10)) is not None


def test_compute_kpi_window_single_day(service: AnalyticsService, fake_uow: UnitOfWork) -> None:
    with fake_uow as uow:
        uow.analytics.upsert_sessions_day(_sessions(day=DAY, sessions=500))

    result = service.compute_kpi_window(LUBELIFE, since=DAY, until=DAY)
    assert result.days_computed == 1
    assert result.days_skipped_no_sessions == 0


def test_compute_kpi_window_rejects_inverted_range(service: AnalyticsService) -> None:
    with pytest.raises(ValueError, match="since must be <= until"):
        service.compute_kpi_window(LUBELIFE, since=DAY, until=DAY - timedelta(days=1))


def test_compute_kpi_window_all_skipped_when_no_sessions(
    service: AnalyticsService,
) -> None:
    result = service.compute_kpi_window(LUBELIFE, since=date(2026, 5, 8), until=date(2026, 5, 10))
    assert result.days_computed == 0
    assert result.days_skipped_no_sessions == EXPECTED_THREE_DAYS
