"""Shopify bulk-operations Customer JSON → domain Customer.

The bulk customers query emits one Customer object per JSONL line with
no nested children — there's no `__parentId` to worry about. Each line
parses into a domain `Customer` and the dispatcher upserts it.

Shape differences vs the webhook payload:

  - camelCase (`firstName` not `first_name`).
  - `legacyResourceId` arrives as a string and must be int-cast.
  - `amountSpent` is a `MoneyV2` object — `{ amount, currencyCode }`.
  - Email-marketing consent comes via `emailMarketingConsent.marketingState`
    (a Shopify `CustomerEmailMarketingState` enum). We treat `"SUBSCRIBED"`
    as `accepts_marketing=True`. All other states (NOT_SUBSCRIBED, PENDING,
    UNSUBSCRIBED, REDACTED, INVALID) → False. This is the canonical source
    of truth — `acceptsMarketing` is deprecated in 2025-04+.
  - `numberOfOrders` is the `orders_count` analog and also arrives as a
    string in some API versions — int-cast defensively.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.models import Customer, CustomerId, StoreId


def _money(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def _required_ts(value: Any, field: str) -> datetime:
    if value is None or value == "":
        raise ValueError(f"required datetime field {field!r} missing or empty")
    return datetime.fromisoformat(value)


def _legacy_id(value: Any) -> int:
    if value is None or value == "":
        raise ValueError("legacyResourceId missing")
    return int(str(value))


def _accepts_marketing(payload: dict[str, Any]) -> bool:
    consent = payload.get("emailMarketingConsent") or {}
    state = consent.get("marketingState")
    return state == "SUBSCRIBED"


def normalize_customer_bulk(store_id: StoreId, payload: dict[str, Any]) -> Customer:
    spent = payload.get("amountSpent") or {}
    return Customer(
        id=CustomerId(0),
        store_id=store_id,
        gid=str(payload["id"]),
        legacy_id=_legacy_id(payload.get("legacyResourceId")),
        email=payload.get("email"),
        phone=payload.get("phone"),
        first_name=payload.get("firstName"),
        last_name=payload.get("lastName"),
        accepts_marketing=_accepts_marketing(payload),
        orders_count=int(payload.get("numberOfOrders") or 0),
        total_spent=_money(spent.get("amount")),
        currency_code=spent.get("currencyCode"),
        created_at=_required_ts(payload.get("createdAt"), "customer.createdAt"),
        updated_at=_required_ts(payload.get("updatedAt"), "customer.updatedAt"),
    )
