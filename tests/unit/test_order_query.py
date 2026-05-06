"""Service-layer unit tests for OrderQueryService (TR-42).

Run against the InMemory repositories so each test is microseconds, not
milliseconds — a 50-test suite stays under a second. The InMemory repos
implement the same Spec filters the SQLAlchemy ones do, so spec coverage
here translates 1:1 to the Postgres path.
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
)
from app.domain.models import (
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    StoreId,
)
from app.domain.repositories import UnitOfWork
from app.domain.specs import OrderSpec
from app.services.order_query import MAX_LIMIT, OrderQueryService

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)

T0 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
SEEDED_ORDER_COUNT = 4


def _line_item(
    *,
    id: int,  # noqa: A002
    order_id: int,
    store_id: StoreId,
    sku: str | None = "SKU-1",
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
        sku=sku,
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
    fulfillment_status: FulfillmentStatus | None = FulfillmentStatus.FULFILLED,
    total_price: Decimal = Decimal("21.98"),
    email: str | None = "buyer@example.com",
    line_items_skus: tuple[str, ...] = ("SKU-1",),
) -> Order:
    line_items = tuple(
        _line_item(id=10_000 + i, order_id=id, store_id=store_id, sku=sku)
        for i, sku in enumerate(line_items_skus)
    )
    return Order(
        id=OrderId(id),
        store_id=store_id,
        customer_id=None,
        gid=f"gid://shopify/Order/{id}",
        legacy_id=id,
        name=f"#TEST-{id}",
        order_number=id,
        email=email,
        financial_status=financial_status,
        fulfillment_status=fulfillment_status,
        currency_code="USD",
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
def service(fake_uow_factory: Callable[[], UnitOfWork]) -> OrderQueryService:
    return OrderQueryService(fake_uow_factory)


@pytest.fixture
def seeded(fake_uow: UnitOfWork) -> UnitOfWork:
    """Seed a small cross-store dataset visible to fake_uow_factory.

    fake_uow and fake_uow_factory share the same InMemoryDatabase via the
    fake_db fixture, so writes here are visible from the service.
    """
    with fake_uow as uow:
        uow.orders.upsert(_order(id=1, store_id=LUBELIFE, processed_at=T0))
        uow.orders.upsert(
            _order(
                id=2,
                store_id=LUBELIFE,
                processed_at=T0 + timedelta(hours=1),
                financial_status=FinancialStatus.PENDING,
                total_price=Decimal("100.00"),
                line_items_skus=("SKU-2",),
            )
        )
        uow.orders.upsert(_order(id=3, store_id=SHOPJO, processed_at=T0 + timedelta(hours=2)))
        uow.orders.upsert(
            _order(
                id=4,
                store_id=SHOPJO,
                processed_at=T0 + timedelta(days=2),
                financial_status=FinancialStatus.REFUNDED,
                total_price=Decimal("500.00"),
                line_items_skus=("SKU-1", "SKU-3"),
            )
        )
    return fake_uow


# ---------------------------------------------------------------------------
# list_orders — basic queries
# ---------------------------------------------------------------------------


def test_list_orders_empty_returns_empty_page(service: OrderQueryService) -> None:
    page = service.list_orders(OrderSpec())
    assert page.items == ()
    assert page.next_cursor is None


def test_list_orders_returns_all_when_spec_unfiltered(
    service: OrderQueryService, seeded: UnitOfWork
) -> None:
    page = service.list_orders(OrderSpec())
    # Ordered by processed_at desc, then id desc — see InMemoryOrderRepository.find.
    assert tuple(o.id for o in page.items) == (4, 3, 2, 1)
    assert page.next_cursor is None


def test_list_orders_filters_by_store_id(service: OrderQueryService, seeded: UnitOfWork) -> None:
    page = service.list_orders(OrderSpec(store_ids=(LUBELIFE,)))
    assert {o.store_id for o in page.items} == {LUBELIFE}
    assert tuple(o.id for o in page.items) == (2, 1)


def test_list_orders_cross_store_filter(service: OrderQueryService, seeded: UnitOfWork) -> None:
    page = service.list_orders(OrderSpec(store_ids=(LUBELIFE, SHOPJO)))
    assert len(page.items) == SEEDED_ORDER_COUNT


# ---------------------------------------------------------------------------
# list_orders — temporal + financial filters
# ---------------------------------------------------------------------------


def test_list_orders_filters_by_since(service: OrderQueryService, seeded: UnitOfWork) -> None:
    page = service.list_orders(OrderSpec(since=T0 + timedelta(hours=1, minutes=30)))
    assert tuple(o.id for o in page.items) == (4, 3)


def test_list_orders_filters_by_until(service: OrderQueryService, seeded: UnitOfWork) -> None:
    page = service.list_orders(OrderSpec(until=T0 + timedelta(hours=1)))
    assert tuple(o.id for o in page.items) == (2, 1)


def test_list_orders_filters_by_financial_status(
    service: OrderQueryService, seeded: UnitOfWork
) -> None:
    page = service.list_orders(OrderSpec(financial_status=FinancialStatus.PAID))
    assert {o.financial_status for o in page.items} == {FinancialStatus.PAID}
    assert tuple(o.id for o in page.items) == (3, 1)


def test_list_orders_filters_by_min_total(service: OrderQueryService, seeded: UnitOfWork) -> None:
    page = service.list_orders(OrderSpec(min_total=Decimal("100.00")))
    assert tuple(o.id for o in page.items) == (4, 2)


# ---------------------------------------------------------------------------
# list_orders — sku filter probes line_items
# ---------------------------------------------------------------------------


def test_list_orders_filters_by_sku_in_line_items(
    service: OrderQueryService, seeded: UnitOfWork
) -> None:
    page = service.list_orders(OrderSpec(sku="SKU-3"))
    assert tuple(o.id for o in page.items) == (4,)


def test_list_orders_sku_match_when_present_in_any_line(
    service: OrderQueryService, seeded: UnitOfWork
) -> None:
    page = service.list_orders(OrderSpec(sku="SKU-1"))
    # Order 4's first line item is SKU-1, and 1, 3 are SKU-1 only.
    assert tuple(o.id for o in page.items) == (4, 3, 1)


# ---------------------------------------------------------------------------
# list_orders — pagination + limit clamping
# ---------------------------------------------------------------------------


def test_list_orders_paginates_via_cursor(service: OrderQueryService, seeded: UnitOfWork) -> None:
    page1 = service.list_orders(OrderSpec(), limit=2)
    assert tuple(o.id for o in page1.items) == (4, 3)
    assert page1.next_cursor is not None

    page2 = service.list_orders(OrderSpec(), limit=2, cursor=page1.next_cursor)
    assert tuple(o.id for o in page2.items) == (2, 1)
    assert page2.next_cursor is None


def test_list_orders_clamps_oversized_limit(service: OrderQueryService, seeded: UnitOfWork) -> None:
    # Build extra orders to exceed MAX_LIMIT.
    for i in range(5, 5 + MAX_LIMIT + 50):
        with service._uow_factory() as uow:  # noqa: SLF001 — test-only access
            uow.orders.upsert(_order(id=i, processed_at=T0 + timedelta(seconds=i)))
    page = service.list_orders(OrderSpec(), limit=10_000)
    assert len(page.items) == MAX_LIMIT


def test_list_orders_clamps_zero_or_negative_limit_to_one(
    service: OrderQueryService, seeded: UnitOfWork
) -> None:
    page = service.list_orders(OrderSpec(), limit=0)
    assert len(page.items) == 1
    page = service.list_orders(OrderSpec(), limit=-50)
    assert len(page.items) == 1


# ---------------------------------------------------------------------------
# get_order_*
# ---------------------------------------------------------------------------


def test_get_order_by_id_returns_match(service: OrderQueryService, seeded: UnitOfWork) -> None:
    order = service.get_order_by_id(OrderId(2))
    assert order is not None
    assert order.id == OrderId(2)
    assert order.financial_status == FinancialStatus.PENDING


def test_get_order_by_id_returns_none_for_missing(service: OrderQueryService) -> None:
    assert service.get_order_by_id(OrderId(9999)) is None


def test_get_order_by_gid_returns_match(service: OrderQueryService, seeded: UnitOfWork) -> None:
    order = service.get_order_by_gid(LUBELIFE, "gid://shopify/Order/1")
    assert order is not None
    assert order.id == OrderId(1)


def test_get_order_by_gid_scoped_to_store(service: OrderQueryService, seeded: UnitOfWork) -> None:
    # Order 1 lives in lubelife — looking it up under shopjo must miss.
    assert service.get_order_by_gid(SHOPJO, "gid://shopify/Order/1") is None


# ---------------------------------------------------------------------------
# count_orders_by_status
# ---------------------------------------------------------------------------


def test_count_orders_by_status_within_window(
    service: OrderQueryService, seeded: UnitOfWork
) -> None:
    counts = service.count_orders_by_status(
        LUBELIFE, since=T0 - timedelta(hours=1), until=T0 + timedelta(days=10)
    )
    assert counts == {FinancialStatus.PAID: 1, FinancialStatus.PENDING: 1}


def test_count_orders_by_status_excludes_other_stores(
    service: OrderQueryService, seeded: UnitOfWork
) -> None:
    counts = service.count_orders_by_status(
        SHOPJO, since=T0 - timedelta(hours=1), until=T0 + timedelta(days=10)
    )
    assert counts == {FinancialStatus.PAID: 1, FinancialStatus.REFUNDED: 1}
