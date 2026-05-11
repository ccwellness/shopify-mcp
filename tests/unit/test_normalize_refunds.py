"""Tests for `normalize_refund_payload` — Shopify GraphQL → domain Refund."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.models import OrderId, StoreId
from app.shopify.normalizers.refunds import normalize_refund_payload

STORE = StoreId(7)
ORDER = OrderId(42)
EXPECTED_LEGACY_ID = 123


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "gid://shopify/Refund/123",
        "legacyResourceId": "123",
        "note": "customer request",
        "createdAt": "2026-05-01T10:30:00+00:00",
        "totalRefundedSet": {
            "shopMoney": {"amount": "12.50", "currencyCode": "USD"},
        },
    }
    base.update(overrides)
    return base


def test_normalize_happy_path_maps_every_field() -> None:
    refund = normalize_refund_payload(STORE, ORDER, _payload())
    assert refund.store_id == STORE
    assert refund.order_id == ORDER
    assert refund.gid == "gid://shopify/Refund/123"
    assert refund.legacy_id == EXPECTED_LEGACY_ID
    assert refund.amount == Decimal("12.50")
    assert refund.currency_code == "USD"
    assert refund.note == "customer request"
    assert refund.created_at == datetime(2026, 5, 1, 10, 30, tzinfo=UTC)


def test_normalize_missing_legacy_id_raises() -> None:
    with pytest.raises(ValueError, match="legacyResourceId missing"):
        normalize_refund_payload(STORE, ORDER, _payload(legacyResourceId=None))


def test_normalize_empty_legacy_id_string_raises() -> None:
    with pytest.raises(ValueError, match="legacyResourceId missing"):
        normalize_refund_payload(STORE, ORDER, _payload(legacyResourceId=""))


def test_normalize_missing_amount_defaults_to_zero() -> None:
    # Shopify sometimes returns an empty shopMoney for fully-reversed refunds.
    refund = normalize_refund_payload(
        STORE,
        ORDER,
        _payload(totalRefundedSet={"shopMoney": {"currencyCode": "USD"}}),
    )
    assert refund.amount == Decimal("0")


def test_normalize_missing_totalrefundedset_defaults_amount_and_usd() -> None:
    refund = normalize_refund_payload(STORE, ORDER, _payload(totalRefundedSet=None))
    assert refund.amount == Decimal("0")
    assert refund.currency_code == "USD"


def test_normalize_note_can_be_none() -> None:
    refund = normalize_refund_payload(STORE, ORDER, _payload(note=None))
    assert refund.note is None


def test_normalize_preserves_currency_code() -> None:
    refund = normalize_refund_payload(
        STORE,
        ORDER,
        _payload(totalRefundedSet={"shopMoney": {"amount": "1.00", "currencyCode": "CAD"}}),
    )
    assert refund.currency_code == "CAD"
