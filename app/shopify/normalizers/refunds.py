"""Shopify GraphQL refund payload → domain Refund.

Refunds aren't in the bulk orders query (Shopify bulk ops don't support
plain list fields). They're fetched per refunded order via a regular
GraphQL query — see `SyncService.sync_refunds`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.models import OrderId, Refund, RefundId, StoreId


def _legacy_id(value: Any) -> int:
    if value is None or value == "":
        raise ValueError("legacyResourceId missing")
    return int(str(value))


def normalize_refund_payload(
    store_id: StoreId,
    order_id: OrderId,
    payload: dict[str, Any],
) -> Refund:
    """Map one Shopify refund object onto the domain `Refund`."""
    shop_money = (payload.get("totalRefundedSet") or {}).get("shopMoney") or {}
    amount_raw = shop_money.get("amount")
    return Refund(
        id=RefundId(0),  # repo assigns
        store_id=store_id,
        order_id=order_id,
        gid=str(payload["id"]),
        legacy_id=_legacy_id(payload.get("legacyResourceId")),
        amount=Decimal(str(amount_raw)) if amount_raw is not None else Decimal("0"),
        currency_code=str(shop_money.get("currencyCode") or "USD"),
        note=payload.get("note"),
        created_at=datetime.fromisoformat(str(payload["createdAt"])),
    )
