"""Contract tests for /api/v1/analytics/daily (TR-31, TR-44)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from http import HTTPStatus

import pytest
from flask.testing import FlaskClient

from app.domain.models import AnalyticsKpiDay, StoreId
from app.domain.repositories import UnitOfWork

LUBELIFE = StoreId(1)
SHOPJO = StoreId(2)
COMPUTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)

EXPECTED_THREE_ROWS = 3
EXPECTED_TWO_ROWS = 2


def _kpi(  # noqa: PLR0913 — kwargs-only by design
    *,
    store_id: StoreId,
    day: date,
    sessions: int | None = 1000,
    orders: int = 25,
    units: int = 50,
    revenue: Decimal = Decimal("500.00"),
    conversion_rate: Decimal | None = Decimal("0.0250"),
    aov: Decimal | None = Decimal("20.00"),
) -> AnalyticsKpiDay:
    return AnalyticsKpiDay(
        store_id=store_id,
        date=day,
        sessions=sessions,
        orders=orders,
        units=units,
        revenue=revenue,
        conversion_rate=conversion_rate,
        aov=aov,
        computed_at=COMPUTED_AT,
    )


@pytest.fixture
def seed(fake_uow: UnitOfWork) -> UnitOfWork:
    with fake_uow as uow:
        uow.analytics.upsert_kpi_day(_kpi(store_id=LUBELIFE, day=date(2026, 5, 9)))
        uow.analytics.upsert_kpi_day(
            _kpi(
                store_id=LUBELIFE,
                day=date(2026, 5, 10),
                sessions=1100,
                orders=30,
                units=60,
                revenue=Decimal("600.00"),
                conversion_rate=Decimal("0.0273"),
                aov=Decimal("20.00"),
            )
        )
        uow.analytics.upsert_kpi_day(_kpi(store_id=SHOPJO, day=date(2026, 5, 10)))
    return fake_uow


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_daily_returns_rows_in_window(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = authed_client.get("/api/v1/analytics/daily?since=2026-05-09&until=2026-05-10")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_json()
    assert body["since"] == "2026-05-09"
    assert body["until"] == "2026-05-10"
    assert len(body["items"]) == EXPECTED_THREE_ROWS


def test_daily_serializes_decimal_fields_as_strings(
    authed_client: FlaskClient, seed: UnitOfWork
) -> None:
    body = authed_client.get("/api/v1/analytics/daily?since=2026-05-09&until=2026-05-09").get_json()
    row = body["items"][0]
    assert row["revenue"] == "500.00"
    assert row["aov"] == "20.00"
    assert row["conversion_rate"] == "0.0250"
    assert row["sessions"] == 1000  # noqa: PLR2004 — explicit fixture value


def test_daily_filters_by_store_id(authed_client: FlaskClient, seed: UnitOfWork) -> None:
    resp = authed_client.get(
        f"/api/v1/analytics/daily?since=2026-05-09&until=2026-05-10&store_id={int(LUBELIFE)}"
    )
    body = resp.get_json()
    assert len(body["items"]) == EXPECTED_TWO_ROWS
    assert {r["store_id"] for r in body["items"]} == {int(LUBELIFE)}


def test_daily_empty_window_returns_empty_items(authed_client: FlaskClient) -> None:
    body = authed_client.get("/api/v1/analytics/daily?since=2026-05-01&until=2026-05-02").get_json()
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Bad input
# ---------------------------------------------------------------------------


def test_daily_missing_since_returns_400(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/analytics/daily?until=2026-05-10")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "since" in resp.get_json()["error"]


def test_daily_malformed_date_returns_400(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/analytics/daily?since=notadate&until=2026-05-10")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "YYYY-MM-DD" in resp.get_json()["error"]


def test_daily_inverted_window_returns_400(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/analytics/daily?since=2026-05-10&until=2026-05-09")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "since must be <= until" in resp.get_json()["error"]


def test_daily_non_integer_store_id_returns_400(authed_client: FlaskClient) -> None:
    resp = authed_client.get(
        "/api/v1/analytics/daily?since=2026-05-09&until=2026-05-10&store_id=abc"
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# Auth + audit
# ---------------------------------------------------------------------------


def test_daily_requires_auth(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.get("/api/v1/analytics/daily?since=2026-05-09&until=2026-05-10")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_daily_writes_audit_row(authed_client: FlaskClient, fake_uow: UnitOfWork) -> None:
    authed_client.get("/api/v1/analytics/daily?since=2026-05-09&until=2026-05-10")
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert len(rows) == 1
    assert rows[0].route_or_tool == "/api/v1/analytics/daily"
