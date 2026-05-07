"""Contract tests for /api/v1/inventory/low-stock (TR-44)."""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus

import pytest
from flask.testing import FlaskClient

from app.domain.models import (
    InventoryItem,
    InventoryItemId,
    InventoryLevel,
    InventoryLevelId,
    LocationId,
    StoreId,
)
from app.domain.repositories import UnitOfWork
from app.services.inventory_reporting import DEFAULT_LOW_STOCK_THRESHOLD

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)
LOC_A = LocationId(10)
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


def _level(  # noqa: PLR0913 — test builder
    *,
    id: int,  # noqa: A002
    item_id: int,
    store_id: StoreId = LUBELIFE,
    location_id: LocationId = LOC_A,
    available: int | None,
) -> InventoryLevel:
    return InventoryLevel(
        id=InventoryLevelId(id),
        store_id=store_id,
        inventory_item_id=InventoryItemId(item_id),
        location_id=location_id,
        available=available,
        on_hand=None,
        committed=None,
        incoming=None,
        updated_at=T0,
    )


@pytest.fixture
def seed(fake_uow: UnitOfWork) -> UnitOfWork:
    with fake_uow as uow:
        uow.inventory.upsert_item(_item(id=100, sku="SKU-A"))
        uow.inventory.upsert_item(_item(id=200, sku="SKU-B"))
        uow.inventory.upsert_item(_item(id=300, store_id=SHOPJO, sku="SKU-C"))
        uow.inventory.upsert_level(_level(id=1, item_id=100, available=2))
        uow.inventory.upsert_level(_level(id=2, item_id=100, available=15))
        uow.inventory.upsert_level(_level(id=3, item_id=200, available=0))
        uow.inventory.upsert_level(_level(id=4, item_id=300, store_id=SHOPJO, available=4))
    return fake_uow


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_low_stock_empty(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/inventory/low-stock")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_json()
    assert body["items"] == []
    assert body["next_cursor"] is None
    assert body["threshold"] == DEFAULT_LOW_STOCK_THRESHOLD


def test_low_stock_returns_seeded(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = authed_client.get("/api/v1/inventory/low-stock")
    assert resp.status_code == HTTPStatus.OK
    ids = {item["id"] for item in resp.get_json()["items"]}
    assert ids == {1, 3, 4}


def test_low_stock_serializes_keys(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    body = authed_client.get("/api/v1/inventory/low-stock").get_json()
    one = body["items"][0]
    assert set(one.keys()) >= {
        "id",
        "store_id",
        "inventory_item_id",
        "location_id",
        "available",
        "on_hand",
        "committed",
        "incoming",
        "updated_at",
    }


def test_low_stock_filters_by_store_id(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = authed_client.get(f"/api/v1/inventory/low-stock?store_id={int(LUBELIFE)}")
    body = resp.get_json()
    assert {item["store_id"] for item in body["items"]} == {int(LUBELIFE)}
    assert {item["id"] for item in body["items"]} == {1, 3}


def test_low_stock_threshold_is_strict_less_than(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    # threshold=2 → only the 0-stock level matches.
    threshold = 2
    body = authed_client.get(f"/api/v1/inventory/low-stock?threshold={threshold}").get_json()
    assert {item["id"] for item in body["items"]} == {3}
    assert body["threshold"] == threshold


def test_low_stock_filters_by_sku(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    body = authed_client.get("/api/v1/inventory/low-stock?sku=SKU-B").get_json()
    assert {item["id"] for item in body["items"]} == {3}


def test_low_stock_paginates(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    PAGE_SIZE = 2
    page1 = authed_client.get(f"/api/v1/inventory/low-stock?limit={PAGE_SIZE}").get_json()
    assert len(page1["items"]) == PAGE_SIZE
    assert page1["next_cursor"] is not None

    page2 = authed_client.get(
        f"/api/v1/inventory/low-stock?limit={PAGE_SIZE}&cursor={page1['next_cursor']}"
    ).get_json()
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None


# ---------------------------------------------------------------------------
# bad input → 400
# ---------------------------------------------------------------------------


def test_low_stock_rejects_negative_threshold(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/inventory/low-stock?threshold=-5")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "non-negative" in resp.get_json()["error"]


def test_low_stock_rejects_non_integer_threshold(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/inventory/low-stock?threshold=abc")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "threshold" in resp.get_json()["error"]


def test_low_stock_rejects_non_integer_store_id(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/inventory/low-stock?store_id=foo")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# Auth + audit (smoke — full coverage in test_api_auth.py)
# ---------------------------------------------------------------------------


def test_low_stock_requires_auth(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.get("/api/v1/inventory/low-stock")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_low_stock_writes_audit_row(authed_client: FlaskClient, fake_uow: UnitOfWork) -> None:
    authed_client.get("/api/v1/inventory/low-stock?threshold=5")
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert len(rows) == 1
    assert rows[0].route_or_tool == "/api/v1/inventory/low-stock"
    assert rows[0].status_code == HTTPStatus.OK
