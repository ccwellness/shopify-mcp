"""Contract tests for /api/v1/subscriptions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from flask.testing import FlaskClient

from app.domain.enums import SubscriptionProvider, SubscriptionStatus
from app.domain.models import (
    CustomerId,
    StoreId,
    SubscriptionContract,
    SubscriptionContractId,
)
from app.domain.repositories import UnitOfWork

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)
T0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

DEFAULT_LIMIT = 50  # mirrors app.services.subscription_query.DEFAULT_LIMIT


def _contract(  # noqa: PLR0913 — test factory; explicit kwargs are clearer than a builder.
    *,
    id: int,  # noqa: A002
    store_id: StoreId = LUBELIFE,
    provider: SubscriptionProvider = SubscriptionProvider.ORDERGROOVE,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    customer_id: CustomerId | None = None,
    updated_at: datetime = T0,
) -> SubscriptionContract:
    return SubscriptionContract(
        id=SubscriptionContractId(id),
        store_id=store_id,
        customer_id=customer_id,
        provider=provider,
        provider_contract_id=f"og-{id}",
        gid=f"gid://shopify/SubscriptionContract/{id}",
        legacy_id=id,
        status=status,
        next_billing_date=None,
        frequency_interval="month",
        frequency_count=3,
        currency_code="USD",
        created_at=T0,
        updated_at=updated_at,
    )


@pytest.fixture
def seed(fake_uow: UnitOfWork) -> UnitOfWork:
    with fake_uow as uow:
        uow.subscriptions.upsert(_contract(id=1, updated_at=T0))
        uow.subscriptions.upsert(
            _contract(
                id=2,
                updated_at=T0 + timedelta(hours=1),
                status=SubscriptionStatus.CANCELLED,
            )
        )
        uow.subscriptions.upsert(
            _contract(
                id=3,
                store_id=SHOPJO,
                updated_at=T0 + timedelta(hours=2),
                customer_id=CustomerId(99),
            )
        )
    return fake_uow


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_list_subscriptions_empty(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/subscriptions")
    assert resp.status_code == HTTPStatus.OK
    assert resp.get_json() == {"items": [], "next_cursor": None, "limit": DEFAULT_LIMIT}


def test_list_subscriptions_orders_by_updated_at_desc(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    body = authed_client.get("/api/v1/subscriptions").get_json()
    assert [item["id"] for item in body["items"]] == [3, 2, 1]
    assert body["next_cursor"] is None


def test_list_subscriptions_serializes_enums_as_strings(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    body = authed_client.get("/api/v1/subscriptions").get_json()
    first = body["items"][0]
    assert first["provider"] == "ordergroove"
    assert first["status"] in {"active", "cancelled"}
    assert first["currency_code"] == "USD"


def test_list_subscriptions_filters_by_store_id(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    resp = authed_client.get(f"/api/v1/subscriptions?store_id={int(LUBELIFE)}")
    body = resp.get_json()
    assert {item["store_id"] for item in body["items"]} == {int(LUBELIFE)}
    assert [item["id"] for item in body["items"]] == [2, 1]


def test_list_subscriptions_filters_by_status(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    body = authed_client.get("/api/v1/subscriptions?status=cancelled").get_json()
    assert [item["id"] for item in body["items"]] == [2]


def test_list_subscriptions_filters_by_provider(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    body = authed_client.get("/api/v1/subscriptions?provider=ordergroove").get_json()
    assert {item["provider"] for item in body["items"]} == {"ordergroove"}


def test_list_subscriptions_filters_by_customer_id(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    body = authed_client.get("/api/v1/subscriptions?customer_id=99").get_json()
    assert [item["id"] for item in body["items"]] == [3]


def test_list_subscriptions_paginates_via_cursor(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    page1 = authed_client.get("/api/v1/subscriptions?limit=2").get_json()
    assert [item["id"] for item in page1["items"]] == [3, 2]
    assert page1["next_cursor"] is not None

    page2 = authed_client.get(
        f"/api/v1/subscriptions?limit=2&cursor={page1['next_cursor']}"
    ).get_json()
    assert [item["id"] for item in page2["items"]] == [1]
    assert page2["next_cursor"] is None


# ---------------------------------------------------------------------------
# Bad input → 400
# ---------------------------------------------------------------------------


def test_list_subscriptions_rejects_bad_status(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/subscriptions?status=banana")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "status" in resp.get_json()["error"]


def test_list_subscriptions_rejects_bad_provider(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/subscriptions?provider=banana")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "provider" in resp.get_json()["error"]


def test_list_subscriptions_rejects_non_integer_store_id(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/subscriptions?store_id=not-an-int")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_list_subscriptions_rejects_non_integer_customer_id(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/subscriptions?customer_id=not-an-int")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
