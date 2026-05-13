"""Shopify bulk-operations order JSON → domain Order.

Webhook payloads are REST-shaped (snake_case, money as plain strings).
Bulk results are GraphQL-shaped (camelCase, `MoneyV2`/`MoneyBag`,
`displayFinancialStatus` SCREAMING_SNAKE). This normalizer handles the
bulk shape and emits the same `NormalizedOrder` dataclass as the webhook
normalizer, so the dispatcher's customer-then-order upsert flow is reused.

Fulfillments are NOT included in the bulk query — Shopify bulk ops don't
support plain list-of-object fields, only connections, and Order.fulfillments
is a list. They flow via the fulfillments/* webhooks instead.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.enums import (
    FinancialStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
)
from app.domain.models import (
    Customer,
    CustomerId,
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    OrderShippingAddress,
    ProductId,
    StoreId,
    VariantId,
)
from app.shopify.normalizers.orders import NormalizedOrder

# GraphQL uses SCREAMING_SNAKE for fulfillment status with values like
# PARTIALLY_FULFILLED that don't lower-case onto our enum directly.
_FULFILLMENT_STATUS_MAP = {
    "FULFILLED": FulfillmentStatus.FULFILLED,
    "PARTIALLY_FULFILLED": FulfillmentStatus.PARTIAL,
    "RESTOCKED": FulfillmentStatus.RESTOCKED,
    "UNFULFILLED": FulfillmentStatus.UNFULFILLED,
    # Less-common values fold to the safest known state.
    "IN_PROGRESS": FulfillmentStatus.PARTIAL,
    "ON_HOLD": FulfillmentStatus.UNFULFILLED,
    "OPEN": FulfillmentStatus.UNFULFILLED,
    "PENDING_FULFILLMENT": FulfillmentStatus.UNFULFILLED,
    "SCHEDULED": FulfillmentStatus.UNFULFILLED,
}


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
    return datetime.fromisoformat(value)


def _required_ts(value: Any, field: str) -> datetime:
    parsed = _ts(value)
    if parsed is None:
        raise ValueError(f"required datetime field {field!r} missing or empty")
    return parsed


def _shop_money(money_bag: dict[str, Any] | None, default: str = "0") -> Decimal:
    """Pull `shopMoney.amount` out of a `MoneyBag` (the GraphQL bulk shape)."""
    if not money_bag:
        return Decimal(default)
    inner = money_bag.get("shopMoney") or {}
    return _money(inner.get("amount"), default=default)


def _presentment_money(money_bag: dict[str, Any] | None) -> Decimal | None:
    if not money_bag:
        return None
    inner = money_bag.get("presentmentMoney") or {}
    return _opt_decimal(inner.get("amount"))


def _financial_status(value: str | None) -> FinancialStatus | None:
    if not value:
        return None
    try:
        return FinancialStatus(value.lower())
    except ValueError:
        return None


def _fulfillment_status(value: str | None) -> FulfillmentStatus | None:
    if not value:
        return None
    return _FULFILLMENT_STATUS_MAP.get(value)


def _line_fulfillment_status(value: str | None) -> OrderLineFulfillmentStatus | None:
    if not value:
        return None
    try:
        return OrderLineFulfillmentStatus(value.lower())
    except ValueError:
        # Fall back to None rather than crashing on a fresh API enum value.
        return None


def _legacy_id(value: Any) -> int:
    """`legacyResourceId` arrives as a string in GraphQL responses."""
    if value is None or value == "":
        raise ValueError("legacyResourceId missing")
    return int(str(value))


def _source_name(value: Any) -> str | None:
    """Normalize `sourceName` to UPPER so filters and indicators don't have
    to deal with Shopify's casing inconsistencies (e.g. both 'tiktok' and
    'TikTok' have been seen in the same store's feed)."""
    if value is None or value == "":
        return None
    return str(value).upper()


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------


def _normalize_customer(store_id: StoreId, payload: dict[str, Any]) -> Customer:
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
        # Default False — bulk query doesn't pull email-marketing consent state.
        # Live customers/* webhooks supply the real value.
        accepts_marketing=False,
        orders_count=int(payload.get("numberOfOrders") or 0),
        total_spent=_money(spent.get("amount")),
        currency_code=spent.get("currencyCode"),
        created_at=_required_ts(payload.get("createdAt"), "customer.createdAt"),
        updated_at=_required_ts(payload.get("updatedAt"), "customer.updatedAt"),
    )


# ---------------------------------------------------------------------------
# Line item, shipping address
# ---------------------------------------------------------------------------


def _sum_discount_allocations(allocations: Any) -> Decimal:
    """Sum the `allocatedAmountSet.shopMoney.amount` over all order-level
    discount allocations attached to this line item.

    Shopify reports per-line-item discounts in `totalDiscountSet` and
    order-level discounts (e.g., a coupon or a manual cart discount on a
    draft order) in `discountAllocations`. They are disjoint, so the
    line item's true discount is the sum of both.
    """
    if not isinstance(allocations, list):
        return Decimal("0")
    total = Decimal("0")
    for alloc in allocations:
        if not isinstance(alloc, dict):
            continue
        total += _shop_money(alloc.get("allocatedAmountSet"))
    return total


def _normalize_line_item(
    store_id: StoreId,
    payload: dict[str, Any],
    *,
    variants_by_gid: dict[str, VariantId],
    products_by_gid: dict[str, ProductId],
) -> OrderLineItem:
    variant = payload.get("variant") or {}
    product = payload.get("product") or {}
    variant_gid = variant.get("id") if isinstance(variant, dict) else None
    product_gid = product.get("id") if isinstance(product, dict) else None
    variant_id = variants_by_gid.get(str(variant_gid)) if variant_gid else None
    product_id = products_by_gid.get(str(product_gid)) if product_gid else None
    line_discount = _shop_money(payload.get("totalDiscountSet")) + _sum_discount_allocations(
        payload.get("discountAllocations")
    )
    return OrderLineItem(
        id=OrderLineItemId(0),
        order_id=OrderId(0),
        store_id=store_id,
        variant_id=variant_id,
        product_id=product_id,
        gid=payload.get("id"),
        legacy_id=None,  # bulk lineItems don't expose legacyResourceId
        title=str(payload.get("title") or ""),
        sku=payload.get("sku"),
        vendor=payload.get("vendor"),
        quantity=int(payload.get("quantity") or 1),
        price=_shop_money(payload.get("originalUnitPriceSet")),
        total_discount=line_discount,
        fulfillment_status=_line_fulfillment_status(payload.get("fulfillmentStatus")),
        requires_shipping=bool(payload.get("requiresShipping", True)),
        taxable=bool(payload.get("taxable", True)),
    )


def _normalize_shipping_address(store_id: StoreId, payload: dict[str, Any]) -> OrderShippingAddress:
    name = payload.get("name")
    if not name:
        first = payload.get("firstName") or ""
        last = payload.get("lastName") or ""
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


# ---------------------------------------------------------------------------
# Order — top level
# ---------------------------------------------------------------------------


def normalize_order_bulk(
    store_id: StoreId,
    payload: dict[str, Any],
    *,
    variants_by_gid: dict[str, VariantId] | None = None,
    products_by_gid: dict[str, ProductId] | None = None,
) -> NormalizedOrder:
    variants_by_gid = variants_by_gid or {}
    products_by_gid = products_by_gid or {}

    customer_payload = payload.get("customer")
    customer = (
        _normalize_customer(store_id, customer_payload)
        if isinstance(customer_payload, dict) and customer_payload.get("id")
        else None
    )

    # JSONL grouper attaches LineItem children under `line_items`.
    line_items = tuple(
        _normalize_line_item(
            store_id,
            li,
            variants_by_gid=variants_by_gid,
            products_by_gid=products_by_gid,
        )
        for li in (payload.get("line_items") or [])
        if isinstance(li, dict)
    )

    shipping_payload = payload.get("shippingAddress")
    shipping_address = (
        _normalize_shipping_address(store_id, shipping_payload)
        if isinstance(shipping_payload, dict)
        else None
    )

    # Parse order_number out of `name` (e.g. "#1001") since bulk doesn't expose it.
    name = str(payload.get("name") or "")
    order_number: int | None = None
    if name.startswith("#") and name[1:].isdigit():
        order_number = int(name[1:])

    order = Order(
        id=OrderId(0),
        store_id=store_id,
        customer_id=None,
        gid=str(payload["id"]),
        legacy_id=_legacy_id(payload.get("legacyResourceId")),
        name=name,
        order_number=order_number,
        email=payload.get("email"),
        financial_status=_financial_status(payload.get("displayFinancialStatus")),
        fulfillment_status=_fulfillment_status(payload.get("displayFulfillmentStatus")),
        currency_code=str(payload.get("currencyCode") or "USD"),
        presentment_currency_code=payload.get("presentmentCurrencyCode"),
        subtotal_price=_shop_money(payload.get("subtotalPriceSet")),
        total_price=_shop_money(payload.get("totalPriceSet")),
        total_tax=_shop_money(payload.get("totalTaxSet")),
        total_discounts=_shop_money(payload.get("totalDiscountsSet")),
        total_shipping=_shop_money(payload.get("totalShippingPriceSet")),
        source_name=_source_name(payload.get("sourceName")),
        presentment_subtotal_price=_presentment_money(payload.get("subtotalPriceSet")),
        presentment_total_price=_presentment_money(payload.get("totalPriceSet")),
        processed_at=_required_ts(
            payload.get("processedAt") or payload.get("createdAt"),
            "order.processedAt",
        ),
        cancelled_at=_ts(payload.get("cancelledAt")),
        closed_at=_ts(payload.get("closedAt")),
        created_at=_required_ts(payload.get("createdAt"), "order.createdAt"),
        updated_at=_required_ts(payload.get("updatedAt"), "order.updatedAt"),
        line_items=line_items,
        shipping_address=shipping_address,
        # Fulfillments deferred — bulk can't ship them; webhooks deliver.
        fulfillments=(),
    )
    return NormalizedOrder(customer=customer, order=order)
