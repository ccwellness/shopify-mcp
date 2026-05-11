"""Tests for the refund repository (via the in-memory fake).

The fake mirrors `SqlAlchemyRefundRepository` semantics, so this also
exercises the contract that protocol-conforming repos must satisfy:

- upsert is idempotent by (store_id, gid) and overwrites mutable fields
- list_in_window / sum_in_window use half-open `[since, until)` windows
- list_for_order is ordered by created_at ascending
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.models import OrderId, Refund, RefundId, StoreId
from tests.fakes import InMemoryUnitOfWork

STORE_A = StoreId(1)
STORE_B = StoreId(2)
ORDER_1 = OrderId(101)
ORDER_2 = OrderId(102)

T0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _refund(  # noqa: PLR0913 — test builder; explicit kwargs beat a config dict
    *,
    gid: str = "gid://shopify/Refund/1",
    store_id: StoreId = STORE_A,
    order_id: OrderId = ORDER_1,
    amount: Decimal = Decimal("10.00"),
    created_at: datetime = T0,
    note: str | None = None,
    currency_code: str = "USD",
    legacy_id: int = 1,
) -> Refund:
    return Refund(
        id=RefundId(0),  # repo assigns
        store_id=store_id,
        order_id=order_id,
        gid=gid,
        legacy_id=legacy_id,
        amount=amount,
        currency_code=currency_code,
        note=note,
        created_at=created_at,
    )


@pytest.fixture
def uow(fake_uow: InMemoryUnitOfWork) -> InMemoryUnitOfWork:
    return fake_uow


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


def test_upsert_inserts_new_refund_and_returns_id(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        rid = u.refunds.upsert(_refund(gid="gid://shopify/Refund/aa"))
    assert int(rid) > 0
    with uow as u:
        loaded = u.refunds.get_by_gid(STORE_A, "gid://shopify/Refund/aa")
    assert loaded is not None
    assert loaded.id == rid


def test_upsert_is_idempotent_by_store_gid(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        rid1 = u.refunds.upsert(_refund(gid="gid://shopify/Refund/dup"))
        rid2 = u.refunds.upsert(_refund(gid="gid://shopify/Refund/dup"))
    # Re-upserting the same (store_id, gid) returns the same row id.
    assert rid1 == rid2


def test_upsert_overwrites_mutable_fields(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        u.refunds.upsert(
            _refund(gid="gid://shopify/Refund/x", amount=Decimal("5.00"), note="first")
        )
        u.refunds.upsert(
            _refund(
                gid="gid://shopify/Refund/x",
                amount=Decimal("17.99"),
                note="corrected",
                currency_code="CAD",
            )
        )
    with uow as u:
        loaded = u.refunds.get_by_gid(STORE_A, "gid://shopify/Refund/x")
    assert loaded is not None
    assert loaded.amount == Decimal("17.99")
    assert loaded.note == "corrected"
    assert loaded.currency_code == "CAD"


def test_upsert_same_gid_different_store_stays_distinct(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        rid_a = u.refunds.upsert(_refund(gid="gid://shopify/Refund/shared", store_id=STORE_A))
        rid_b = u.refunds.upsert(_refund(gid="gid://shopify/Refund/shared", store_id=STORE_B))
    assert rid_a != rid_b


# ---------------------------------------------------------------------------
# get_by_gid / list_for_order
# ---------------------------------------------------------------------------


def test_get_by_gid_returns_none_for_unknown(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        assert u.refunds.get_by_gid(STORE_A, "gid://shopify/Refund/missing") is None


def test_get_by_gid_is_scoped_to_store(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        u.refunds.upsert(_refund(gid="gid://shopify/Refund/only-a", store_id=STORE_A))
    with uow as u:
        # Same gid, but the request is for STORE_B — no match.
        assert u.refunds.get_by_gid(STORE_B, "gid://shopify/Refund/only-a") is None


def test_list_for_order_returns_only_that_order_in_chronological_order(
    uow: InMemoryUnitOfWork,
) -> None:
    with uow as u:
        u.refunds.upsert(_refund(gid="r-1", order_id=ORDER_1, created_at=T0 + timedelta(hours=2)))
        u.refunds.upsert(_refund(gid="r-2", order_id=ORDER_1, created_at=T0))
        u.refunds.upsert(_refund(gid="r-3", order_id=ORDER_2, created_at=T0))
    with uow as u:
        rows = u.refunds.list_for_order(ORDER_1)
    assert tuple(r.gid for r in rows) == ("r-2", "r-1")  # ascending by created_at


# ---------------------------------------------------------------------------
# list_in_window / sum_in_window
# ---------------------------------------------------------------------------


def test_list_in_window_is_half_open(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        u.refunds.upsert(_refund(gid="r-at-since", created_at=T0))
        u.refunds.upsert(_refund(gid="r-at-until", created_at=T0 + timedelta(days=1)))
        u.refunds.upsert(_refund(gid="r-inside", created_at=T0 + timedelta(hours=6)))

    with uow as u:
        rows = u.refunds.list_in_window(STORE_A, T0, T0 + timedelta(days=1))

    # `since` inclusive, `until` exclusive — the boundary refund at `until` is dropped.
    assert {r.gid for r in rows} == {"r-at-since", "r-inside"}


def test_sum_in_window_sums_only_matching_store_and_window(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        u.refunds.upsert(_refund(gid="a", amount=Decimal("10.00")))
        u.refunds.upsert(
            _refund(
                gid="b",
                amount=Decimal("5.50"),
                created_at=T0 + timedelta(hours=1),
            )
        )
        # Wrong store — excluded.
        u.refunds.upsert(_refund(gid="c", store_id=STORE_B, amount=Decimal("999.00")))
        # Out of window — excluded.
        u.refunds.upsert(
            _refund(gid="d", amount=Decimal("999.00"), created_at=T0 + timedelta(days=30))
        )

    with uow as u:
        total = u.refunds.sum_in_window(STORE_A, T0, T0 + timedelta(days=1))

    assert total == Decimal("15.50")


def test_sum_in_window_returns_zero_when_empty(uow: InMemoryUnitOfWork) -> None:
    with uow as u:
        total = u.refunds.sum_in_window(STORE_A, T0, T0 + timedelta(days=1))
    assert total == Decimal("0")
