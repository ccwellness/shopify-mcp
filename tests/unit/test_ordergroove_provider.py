"""Unit tests for `OrderGrooveProvider` and its record normalizer.

Mocks the HTTP client so the tests run without network. Verifies status
mapping, period-code mapping, customer FK resolution, and GID handling.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.enums import (
    SubscriptionProvider as SubscriptionProviderEnum,
)
from app.domain.enums import (
    SubscriptionStatus,
)
from app.domain.models import CustomerId, StoreId
from app.services.subscriptions.ordergroove import OrderGrooveProvider, _normalize

STORE = StoreId(5)

EXPECTED_FREQUENCY_COUNT = 3
EXPECTED_LEGACY_ID = 159590777071
EXPECTED_CUSTOMER_ID = 42


def _sample_record(**overrides: Any) -> dict[str, Any]:
    """Realistic record (sanitized) from the lubelife probe."""
    base: dict[str, Any] = {
        "customer": "9303754178799",
        "merchant": "22f1e81e560911ed83fdcaf5d4ece3ab",
        "product": "46639802613999",
        "extra_data": {"shopify_contract_id": "gid://shopify/SubscriptionContract/159590777071"},
        "public_id": "bbf61f355f0f44ff847a8c2e0c92d43b",
        "quantity": 2,
        "frequency_days": 90,
        "every": 3,
        "every_period": 3,
        "start_date": "2026-05-11",
        "cancelled": None,
        "merchant_order_id": "#42186",
        "created": "2026-05-11 14:07:35",
        "updated": "2026-05-11 14:07:35",
        "live": True,
        "external_id": "gid://shopify/SubscriptionContract/159590777071",
        "currency_code": "USD",
    }
    base.update(overrides)
    return base


def _lookup_returns_42(_: str) -> CustomerId | None:
    return CustomerId(EXPECTED_CUSTOMER_ID)


def _lookup_returns_none(_: str) -> CustomerId | None:
    return None


# ---------------------------------------------------------------------------
# _normalize — record-by-record mapping
# ---------------------------------------------------------------------------


def test_normalize_active_record_maps_every_field() -> None:
    out = _normalize(_sample_record(), store_id=STORE, customer_lookup=_lookup_returns_42)

    assert out.store_id == STORE
    assert out.customer_id == CustomerId(EXPECTED_CUSTOMER_ID)
    assert out.provider is SubscriptionProviderEnum.ORDERGROOVE
    assert out.provider_contract_id == "bbf61f355f0f44ff847a8c2e0c92d43b"
    assert out.gid == "gid://shopify/SubscriptionContract/159590777071"
    assert out.legacy_id == EXPECTED_LEGACY_ID
    assert out.status is SubscriptionStatus.ACTIVE
    assert out.frequency_interval == "month"  # every_period=3
    assert out.frequency_count == EXPECTED_FREQUENCY_COUNT  # every=3
    assert out.currency_code == "USD"
    # 2026-05-11 14:07:35 → naive UTC parse
    assert out.created_at == datetime(2026, 5, 11, 14, 7, 35, tzinfo=UTC)
    assert out.updated_at == datetime(2026, 5, 11, 14, 7, 35, tzinfo=UTC)
    # OG list endpoint doesn't expose a single "next" date — left None.
    assert out.next_billing_date is None


def test_normalize_status_cancelled_when_cancelled_field_set() -> None:
    out = _normalize(
        _sample_record(cancelled="2026-05-09 10:00:00", live=True),
        store_id=STORE,
        customer_lookup=_lookup_returns_42,
    )
    assert out.status is SubscriptionStatus.CANCELLED


def test_normalize_status_paused_when_live_false_and_not_cancelled() -> None:
    out = _normalize(
        _sample_record(live=False, cancelled=None),
        store_id=STORE,
        customer_lookup=_lookup_returns_42,
    )
    assert out.status is SubscriptionStatus.PAUSED


def test_normalize_status_cancelled_takes_priority_over_live_false() -> None:
    out = _normalize(
        _sample_record(live=False, cancelled="2026-05-09 10:00:00"),
        store_id=STORE,
        customer_lookup=_lookup_returns_42,
    )
    assert out.status is SubscriptionStatus.CANCELLED


def test_normalize_customer_id_none_when_lookup_misses() -> None:
    out = _normalize(_sample_record(), store_id=STORE, customer_lookup=_lookup_returns_none)
    assert out.customer_id is None


def test_normalize_customer_id_none_when_og_customer_is_missing() -> None:
    out = _normalize(
        _sample_record(customer=None), store_id=STORE, customer_lookup=_lookup_returns_42
    )
    # Lookup is never called; result is None.
    assert out.customer_id is None


@pytest.mark.parametrize(
    ("code", "expected"),
    [(1, "day"), (2, "week"), (3, "month"), (4, "year")],
)
def test_normalize_frequency_interval_maps_period_code(code: int, expected: str) -> None:
    out = _normalize(
        _sample_record(every_period=code, every=1),
        store_id=STORE,
        customer_lookup=_lookup_returns_42,
    )
    assert out.frequency_interval == expected
    assert out.frequency_count == 1


def test_normalize_frequency_interval_none_when_period_unknown() -> None:
    out = _normalize(
        _sample_record(every_period=99),
        store_id=STORE,
        customer_lookup=_lookup_returns_42,
    )
    assert out.frequency_interval is None


def test_normalize_falls_back_to_extra_data_when_external_id_missing() -> None:
    out = _normalize(
        _sample_record(
            external_id=None,
            extra_data={"shopify_contract_id": "gid://shopify/SubscriptionContract/12345"},
        ),
        store_id=STORE,
        customer_lookup=_lookup_returns_42,
    )
    assert out.gid == "gid://shopify/SubscriptionContract/12345"
    expected_id = 12345
    assert out.legacy_id == expected_id


def test_normalize_gid_none_when_both_external_and_extra_missing() -> None:
    out = _normalize(
        _sample_record(external_id=None, extra_data={}),
        store_id=STORE,
        customer_lookup=_lookup_returns_42,
    )
    assert out.gid is None
    assert out.legacy_id is None


def test_normalize_unparseable_timestamp_falls_back_to_now() -> None:
    # Garbage timestamps shouldn't crash the sync; we drop into now() and
    # log nothing — the row still lands. Verify the year is at least 2026
    # (sanity: we're not in 1970).
    out = _normalize(
        _sample_record(created="not a date", updated="also not a date"),
        store_id=STORE,
        customer_lookup=_lookup_returns_42,
    )
    min_year = 2026
    assert out.created_at.year >= min_year


# ---------------------------------------------------------------------------
# OrderGrooveProvider.iter_active — wires client + normalizer
# ---------------------------------------------------------------------------


class _StubClient:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def iter_subscriptions(self, *, page_size: int = 100) -> Iterator[dict[str, Any]]:
        yield from self._records


def test_iter_active_yields_one_contract_per_client_record() -> None:
    client = _StubClient([_sample_record(public_id="aaa"), _sample_record(public_id="bbb")])
    provider = OrderGrooveProvider(
        client=client,  # type: ignore[arg-type]
        store_id=STORE,
        customer_lookup=_lookup_returns_42,
    )
    out = list(provider.iter_active())
    expected_count = 2
    assert len(out) == expected_count
    assert [c.provider_contract_id for c in out] == ["aaa", "bbb"]
