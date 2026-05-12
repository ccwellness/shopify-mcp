"""Unit test for `SyncService.sync_sessions` (TR-29).

Drives the service with a stub Shopify client whose canned response
mirrors the Phase 0 probe payload. Verifies the upsert lands in the
in-memory analytics repo and that `sync_state.SESSIONS` advances.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AnalyticsSource, SubscriptionProvider, SyncResource
from app.domain.models import Store, StoreId
from app.domain.repositories import UnitOfWork
from app.services.sync import SyncService
from app.shopify.config import StoreConfig

STORE_KEY = "lubelife"
T0 = datetime(2026, 5, 12, tzinfo=__import__("datetime").UTC)

EXPECTED_THREE_DAYS = 3


def _store_config() -> StoreConfig:
    return StoreConfig(
        store_key=STORE_KEY,
        shop_domain="lubelife.myshopify.com",
        client_id="cid",
        client_secret="csecret",  # noqa: S106 — test fixture
        webhook_secret="wsecret",  # noqa: S106 — test fixture
        plus=False,
        subscription_provider=SubscriptionProvider.UNKNOWN,
        read_only=True,
    )


def _seed_store(uow: UnitOfWork) -> StoreId:
    with uow as u:
        u.stores.upsert(
            Store(
                id=StoreId(0),
                store_key=STORE_KEY,
                shop_domain="lubelife.myshopify.com",
                display_name="lubelife",
                plus=False,
                subscription_provider=SubscriptionProvider.UNKNOWN,
                read_only=True,
                active=True,
                timezone=None,
                currency_code="USD",
                created_at=T0,
                updated_at=T0,
            )
        )
        u.commit()
        loaded = u.stores.get_by_key(STORE_KEY)
    assert loaded is not None
    return loaded.id


class _StubShopifyClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def query(
        self,
        store_key: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_mutation: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((store_key, query))
        return self._response


@pytest.fixture
def stub_response() -> dict[str, Any]:
    return {
        "shopifyqlQuery": {
            "parseErrors": [],
            "tableData": {
                "columns": [
                    {"name": "day", "dataType": "date", "displayName": "Day", "subType": None},
                    {
                        "name": "total_sales",
                        "dataType": "money",
                        "displayName": "Sales",
                        "subType": None,
                    },
                    {
                        "name": "orders",
                        "dataType": "number",
                        "displayName": "Orders",
                        "subType": None,
                    },
                    {
                        "name": "sessions",
                        "dataType": "number",
                        "displayName": "Sessions",
                        "subType": None,
                    },
                ],
                "rows": [
                    ["2026-05-09", "1100.00", 25, 900],
                    ["2026-05-10", "950.50", 22, 870],
                    ["2026-05-11", "1300.75", 28, 1020],
                ],
            },
        }
    }


def test_sync_sessions_upserts_one_row_per_day(
    fake_uow_factory: Callable[[], UnitOfWork],
    stub_response: dict[str, Any],
) -> None:
    store_id = _seed_store(fake_uow_factory())
    client = _StubShopifyClient(stub_response)
    svc = SyncService(
        uow_factory=fake_uow_factory,
        shopify_client=client,  # type: ignore[arg-type]
        bulk_client=object(),  # type: ignore[arg-type] — sync_sessions doesn't use bulk
        store_configs={STORE_KEY: _store_config()},
    )

    result = svc.sync_sessions(STORE_KEY, days_back=7)

    assert result.upserted == EXPECTED_THREE_DAYS
    assert result.resource == SyncResource.SESSIONS

    # Three rows in analytics, all marked shopifyql.
    with fake_uow_factory() as uow:
        rows = sorted(
            (uow.analytics.get_sessions_day(store_id, date(2026, 5, d)) for d in (9, 10, 11)),
            key=lambda r: r.date if r else date.min,
        )
    assert all(r is not None for r in rows)
    assert rows[0].total_sales == Decimal("1100.00")
    assert rows[1].sessions == 870  # noqa: PLR2004
    assert rows[2].orders == 28  # noqa: PLR2004
    assert all(r.source == AnalyticsSource.SHOPIFYQL for r in rows if r)

    # sync_state advances.
    with fake_uow_factory() as uow:
        ss = uow.sync_state.get(store_id, SyncResource.SESSIONS)
    assert ss is not None
    assert ss.last_completed_at is not None


def test_sync_sessions_rejects_zero_or_negative_days(
    fake_uow_factory: Callable[[], UnitOfWork],
    stub_response: dict[str, Any],
) -> None:
    _seed_store(fake_uow_factory())
    svc = SyncService(
        uow_factory=fake_uow_factory,
        shopify_client=_StubShopifyClient(stub_response),  # type: ignore[arg-type]
        bulk_client=object(),  # type: ignore[arg-type]
        store_configs={STORE_KEY: _store_config()},
    )
    with pytest.raises(ValueError, match="days_back must be > 0"):
        svc.sync_sessions(STORE_KEY, days_back=0)


def test_sync_sessions_emits_query_with_days_back_window(
    fake_uow_factory: Callable[[], UnitOfWork],
    stub_response: dict[str, Any],
) -> None:
    _seed_store(fake_uow_factory())
    client = _StubShopifyClient(stub_response)
    svc = SyncService(
        uow_factory=fake_uow_factory,
        shopify_client=client,  # type: ignore[arg-type]
        bulk_client=object(),  # type: ignore[arg-type]
        store_configs={STORE_KEY: _store_config()},
    )

    svc.sync_sessions(STORE_KEY, days_back=14)

    # The query the stub recorded should mention -14d.
    assert client.calls
    assert "-14d" in client.calls[0][1]
    assert "UNTIL -1d" in client.calls[0][1]
