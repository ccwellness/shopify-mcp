"""Shopify webhook order payload → domain Order (with nested children).

Webhooks deliver REST-shaped JSON (not GraphQL). Money fields are strings,
datetime strings are ISO 8601 with offsets, and identifiers come as both
the legacy integer (`id`) and an `admin_graphql_api_id` (the GID).

This normalizer never touches the DB. It returns a `NormalizedOrder` with
the optional Customer detached from the Order: the dispatcher upserts the
customer first to get its DB id, then fills it onto the Order before the
order's own upsert.

Variants and locations are NOT resolved here — line items carry None for
`variant_id`/`product_id` and fulfillments carry None for `location_id`.
Catalog and locations sync (separate jobs) populate those FK columns later;
the GIDs / legacy_ids on the line items remain as the linkage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.enums import (
    FinancialStatus,
    FulfillmentExecutionStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
    ShipmentStatus,
)
from app.domain.models import (
    Customer,
    CustomerId,
    Fulfillment,
    FulfillmentId,
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    OrderShippingAddress,
    StoreId,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedOrder:
    """Output of `normalize_order_webhook`. Customer is detached from Order
    so the dispatcher can upsert it first."""

    customer: Customer | None
    order: Order


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------


def _money(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def _opt_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    # Shopify uses ISO 8601 with timezone, e.g. "2026-04-29T15:00:00-04:00".
    # Python 3.11+ fromisoformat handles this.
    return datetime.fromisoformat(value)


def _required_ts(value: Any, field: str) -> datetime:
    parsed = _ts(value)
    if parsed is None:
        raise ValueError(f"required datetime field {field!r} missing or empty")
    return parsed


def _gid(payload: dict[str, Any], kind: str) -> str:
    """Prefer the explicit `admin_graphql_api_id`; fall back to a synthesized GID."""
    gid = payload.get("admin_graphql_api_id")
    if gid:
        return str(gid)
    legacy_id = payload.get("id")
    if legacy_id is None:
        raise ValueError(f"{kind} payload missing both admin_graphql_api_id and id")
    return f"gid://shopify/{kind}/{legacy_id}"


def _shop_money(set_field: dict[str, Any] | None, default: str = "0") -> Decimal:
    """Pull the shop-currency amount from a Shopify `*_set` block."""
    if not set_field:
        return Decimal(default)
    inner = set_field.get("shop_money") or {}
    return _money(inner.get("amount"), default=default)


def _presentment_money(set_field: dict[str, Any] | None) -> Decimal | None:
    if not set_field:
        return None
    inner = set_field.get("presentment_money") or {}
    return _opt_decimal(inner.get("amount"))


def _source_name(value: Any) -> str | None:
    """UPPER-case the source name to match bulk-normalizer canonical form
    (Shopify ships both 'tiktok' and 'TikTok' so we normalize on read)."""
    if value is None or value == "":
        return None
    return str(value).upper()


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------


def _normalize_customer(store_id: StoreId, payload: dict[str, Any]) -> Customer:
    return Customer(
        id=CustomerId(0),
        store_id=store_id,
        gid=_gid(payload, "Customer"),
        legacy_id=int(payload["id"]),
        email=payload.get("email"),
        phone=payload.get("phone"),
        first_name=payload.get("first_name"),
        last_name=payload.get("last_name"),
        accepts_marketing=bool(payload.get("accepts_marketing")),
        orders_count=int(payload.get("orders_count") or 0),
        total_spent=_money(payload.get("total_spent")),
        currency_code=payload.get("currency"),
        created_at=_required_ts(payload.get("created_at"), "customer.created_at"),
        updated_at=_required_ts(payload.get("updated_at"), "customer.updated_at"),
    )


# ---------------------------------------------------------------------------
# Line items, shipping address, fulfillments
# ---------------------------------------------------------------------------


def _sum_webhook_discount_allocations(allocations: Any) -> Decimal:
    """REST shape — sum `discount_allocations[].amount` (order-level discounts
    allocated to this line item). Disjoint from `total_discount` per Shopify's
    payload contract; the line item's true total discount is the sum."""
    if not isinstance(allocations, list):
        return Decimal("0")
    total = Decimal("0")
    for alloc in allocations:
        if isinstance(alloc, dict):
            total += _money(alloc.get("amount"))
    return total


def _normalize_line_item(store_id: StoreId, payload: dict[str, Any]) -> OrderLineItem:
    line_discount = _money(payload.get("total_discount")) + _sum_webhook_discount_allocations(
        payload.get("discount_allocations")
    )
    return OrderLineItem(
        id=OrderLineItemId(0),
        order_id=OrderId(0),  # filled by ORM relationship cascade
        store_id=store_id,
        variant_id=None,  # resolved by catalog sync (TODO)
        product_id=None,  # resolved by catalog sync (TODO)
        gid=payload.get("admin_graphql_api_id"),
        legacy_id=int(payload["id"]) if payload.get("id") is not None else None,
        title=str(payload.get("title") or ""),
        sku=payload.get("sku"),
        vendor=payload.get("vendor"),
        quantity=int(payload.get("quantity") or 1),
        price=_money(payload.get("price")),
        total_discount=line_discount,
        fulfillment_status=(
            OrderLineFulfillmentStatus(payload["fulfillment_status"])
            if payload.get("fulfillment_status")
            else None
        ),
        requires_shipping=bool(payload.get("requires_shipping", True)),
        taxable=bool(payload.get("taxable", True)),
    )


def _normalize_shipping_address(store_id: StoreId, payload: dict[str, Any]) -> OrderShippingAddress:
    name = payload.get("name")
    if not name:
        first = payload.get("first_name") or ""
        last = payload.get("last_name") or ""
        joined = f"{first} {last}".strip()
        name = joined or None
    return OrderShippingAddress(
        order_id=OrderId(0),
        store_id=store_id,
        name=name,
        company=payload.get("company"),
        address1=payload.get("address1"),
        address2=payload.get("address2"),
        city=payload.get("city"),
        province=payload.get("province"),
        country=payload.get("country"),
        zip=payload.get("zip"),
        phone=payload.get("phone"),
        latitude=_opt_decimal(payload.get("latitude")),
        longitude=_opt_decimal(payload.get("longitude")),
    )


def _normalize_fulfillment(store_id: StoreId, payload: dict[str, Any]) -> Fulfillment:
    return Fulfillment(
        id=FulfillmentId(0),
        order_id=OrderId(0),
        store_id=store_id,
        location_id=None,  # resolved by locations sync (TODO)
        gid=_gid(payload, "Fulfillment"),
        legacy_id=int(payload["id"]),
        status=FulfillmentExecutionStatus(payload["status"]),
        shipment_status=(
            ShipmentStatus(payload["shipment_status"]) if payload.get("shipment_status") else None
        ),
        tracking_company=payload.get("tracking_company"),
        tracking_number=payload.get("tracking_number"),
        tracking_url=payload.get("tracking_url"),
        created_at=_required_ts(payload.get("created_at"), "fulfillment.created_at"),
        updated_at=_required_ts(
            payload.get("updated_at") or payload.get("created_at"), "fulfillment.updated_at"
        ),
    )


# ---------------------------------------------------------------------------
# Order — top level
# ---------------------------------------------------------------------------


def normalize_order_webhook(store_id: StoreId, payload: dict[str, Any]) -> NormalizedOrder:
    """Turn a Shopify orders/* webhook payload into domain objects."""
    customer_payload = payload.get("customer")
    customer = (
        _normalize_customer(store_id, customer_payload)
        if isinstance(customer_payload, dict) and customer_payload.get("id")
        else None
    )

    line_items = tuple(
        _normalize_line_item(store_id, li)
        for li in (payload.get("line_items") or [])
        if isinstance(li, dict)
    )

    shipping_payload = payload.get("shipping_address")
    shipping_address = (
        _normalize_shipping_address(store_id, shipping_payload)
        if isinstance(shipping_payload, dict)
        else None
    )

    fulfillments = tuple(
        _normalize_fulfillment(store_id, f)
        for f in (payload.get("fulfillments") or [])
        if isinstance(f, dict)
    )

    financial = payload.get("financial_status")
    fulfillment = payload.get("fulfillment_status")

    order = Order(
        id=OrderId(0),
        store_id=store_id,
        customer_id=None,  # dispatcher fills this after customer upsert
        gid=_gid(payload, "Order"),
        legacy_id=int(payload["id"]),
        name=str(payload.get("name") or f"#{payload.get('order_number') or payload['id']}"),
        order_number=int(payload["order_number"]) if payload.get("order_number") else None,
        email=payload.get("email"),
        financial_status=FinancialStatus(financial) if financial else None,
        fulfillment_status=FulfillmentStatus(fulfillment) if fulfillment else None,
        currency_code=str(payload.get("currency") or "USD"),
        presentment_currency_code=payload.get("presentment_currency"),
        subtotal_price=_money(payload.get("subtotal_price")),
        total_price=_money(payload.get("total_price")),
        total_tax=_money(payload.get("total_tax")),
        total_discounts=_money(payload.get("total_discounts")),
        total_shipping=_shop_money(payload.get("total_shipping_price_set")),
        source_name=_source_name(payload.get("source_name")),
        presentment_subtotal_price=_presentment_money(payload.get("subtotal_price_set")),
        presentment_total_price=_presentment_money(payload.get("total_price_set")),
        processed_at=_required_ts(
            payload.get("processed_at") or payload.get("created_at"), "order.processed_at"
        ),
        cancelled_at=_ts(payload.get("cancelled_at")),
        closed_at=_ts(payload.get("closed_at")),
        created_at=_required_ts(payload.get("created_at"), "order.created_at"),
        updated_at=_required_ts(payload.get("updated_at"), "order.updated_at"),
        line_items=line_items,
        shipping_address=shipping_address,
        fulfillments=fulfillments,
    )
    return NormalizedOrder(customer=customer, order=order)
