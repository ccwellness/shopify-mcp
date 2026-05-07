"""Unit tests for InventoryReportingService.list_low_stock (TR-32, TR-42)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from app.domain.models import (
    InventoryItem,
    InventoryItemId,
    InventoryLevel,
    InventoryLevelId,
    LocationId,
    StoreId,
)
from app.domain.repositories import UnitOfWork
from app.services.inventory_reporting import (
    DEFAULT_LOW_STOCK_THRESHOLD,
    MAX_LIMIT,
    InventoryReportingService,
)

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)
LOC_A = LocationId(10)
LOC_B = LocationId(20)
T0 = datetime(2026, 5, 1, tzinfo=UTC)


def _item(*, id: int, store_id: StoreId = LUBELIFE, sku: str | None = None) -> InventoryItem:  # noqa: A002
    return InventoryItem(
        id=InventoryItemId(id),
        store_id=store_id,
        variant_id=None,
        gid=f"gid://shopify/InventoryItem/{id}",
        legacy_id=id,
        sku=sku,
        tracked=True,
    )


def _level(  # noqa: PLR0913 — test builder; explicit kwargs are clearer than a config dict
    *,
    id: int,  # noqa: A002
    item_id: int,
    location_id: LocationId = LOC_A,
    store_id: StoreId = LUBELIFE,
    available: int | None,
    on_hand: int | None = None,
) -> InventoryLevel:
    return InventoryLevel(
        id=InventoryLevelId(id),
        store_id=store_id,
        inventory_item_id=InventoryItemId(item_id),
        location_id=location_id,
        available=available,
        on_hand=on_hand,
        committed=None,
        incoming=None,
        updated_at=T0,
    )


@pytest.fixture
def service(fake_uow_factory: Callable[[], UnitOfWork]) -> InventoryReportingService:
    return InventoryReportingService(fake_uow_factory)


@pytest.fixture
def seeded(fake_uow: UnitOfWork) -> UnitOfWork:
    """Seed inventory: 3 items, 5 levels spanning thresholds + stores + locations."""
    with fake_uow as uow:
        uow.inventory.upsert_item(_item(id=100, sku="SKU-A"))
        uow.inventory.upsert_item(_item(id=200, sku="SKU-B"))
        uow.inventory.upsert_item(_item(id=300, store_id=SHOPJO, sku="SKU-C"))

        uow.inventory.upsert_level(_level(id=1, item_id=100, available=2))  # low (lubelife/A)
        uow.inventory.upsert_level(_level(id=2, item_id=100, location_id=LOC_B, available=15))  # ok
        uow.inventory.upsert_level(_level(id=3, item_id=200, available=0))  # zero (lubelife/A)
        uow.inventory.upsert_level(_level(id=4, item_id=200, available=None))  # null skipped
        uow.inventory.upsert_level(
            _level(id=5, item_id=300, store_id=SHOPJO, available=4)
        )  # low (shopjo/A)
    return fake_uow


def test_list_low_stock_empty(service: InventoryReportingService) -> None:
    page = service.list_low_stock(threshold=10)
    assert page.items == ()
    assert page.next_cursor is None


def test_list_low_stock_returns_below_threshold(
    service: InventoryReportingService, seeded: UnitOfWork
) -> None:
    page = service.list_low_stock(threshold=10)
    ids = {int(level.id) for level in page.items}
    assert ids == {1, 3, 5}  # 2, 0, 4 — all below 10; 15 excluded; null excluded


def test_list_low_stock_excludes_null_available(
    service: InventoryReportingService, seeded: UnitOfWork
) -> None:
    page = service.list_low_stock(threshold=10)
    nulls = [level for level in page.items if level.available is None]
    assert nulls == []


def test_list_low_stock_filters_by_store(
    service: InventoryReportingService, seeded: UnitOfWork
) -> None:
    page = service.list_low_stock(store_ids=(LUBELIFE,), threshold=10)
    assert {int(level.store_id) for level in page.items} == {int(LUBELIFE)}


def test_list_low_stock_filters_by_location(
    service: InventoryReportingService, seeded: UnitOfWork
) -> None:
    page = service.list_low_stock(threshold=10, location_id=LOC_A)
    assert {int(level.location_id) for level in page.items} == {int(LOC_A)}


def test_list_low_stock_filters_by_sku(
    service: InventoryReportingService, seeded: UnitOfWork
) -> None:
    page = service.list_low_stock(threshold=10, sku="SKU-B")
    # Only level 3 (item 200, sku SKU-B) matches.
    ids = {int(level.id) for level in page.items}
    assert ids == {3}


def test_list_low_stock_threshold_is_strict_less_than(
    service: InventoryReportingService, seeded: UnitOfWork
) -> None:
    # threshold=2 means strictly less than 2 — only the 0-stock level matches.
    page = service.list_low_stock(threshold=2)
    ids = {int(level.id) for level in page.items}
    assert ids == {3}


def test_list_low_stock_paginates(service: InventoryReportingService, seeded: UnitOfWork) -> None:
    PAGE_SIZE = 2
    page1 = service.list_low_stock(threshold=10, limit=PAGE_SIZE)
    assert len(page1.items) == PAGE_SIZE
    assert page1.next_cursor is not None

    page2 = service.list_low_stock(threshold=10, limit=PAGE_SIZE, cursor=page1.next_cursor)
    assert len(page2.items) == 1
    assert page2.next_cursor is None
    # Disjoint pages.
    p1_ids = {int(level.id) for level in page1.items}
    p2_ids = {int(level.id) for level in page2.items}
    assert not (p1_ids & p2_ids)


def test_list_low_stock_clamps_oversized_limit(
    service: InventoryReportingService, seeded: UnitOfWork
) -> None:
    page = service.list_low_stock(threshold=10, limit=10_000)
    # 3 matches available; clamping to MAX_LIMIT shouldn't shrink the result here.
    assert len(page.items) <= MAX_LIMIT


def test_list_low_stock_rejects_negative_threshold(
    service: InventoryReportingService,
) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        service.list_low_stock(threshold=-1)


def test_list_low_stock_default_threshold_constant_exists() -> None:
    assert DEFAULT_LOW_STOCK_THRESHOLD > 0
