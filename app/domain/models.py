"""Pure-Python domain models.

Frozen, slotted dataclasses. No SQLAlchemy, no Flask, no I/O — these are
the types repositories return (TR-22) and that services and presentation
layers consume.

Design choices worth knowing:

- **Identity**: every aggregate root has a typed-int alias (e.g. `OrderId`)
  so the type checker catches `find_orders(customer_id)` mistakes.
- **Shopify identifiers** (TR-20): every row carries both `gid` (full
  `gid://shopify/Order/12345`) and `legacy_id` (the parsed numeric tail)
  so we can sort/range on the int and still round-trip the GID for API
  callers.
- **Money**: `Decimal` with the currency_code beside it. We never
  round-trip floats. Money columns are `numeric(19,4)` in the DB (TR-19).
- **Order aggregate** carries its `line_items`, `shipping_address`, and
  `fulfillments` inline as tuples — repositories materialize the whole
  aggregate so callers cannot trigger surprise lazy loads (per design 11A.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import NewType

from app.domain.enums import (
    AnalyticsSource,
    FinancialStatus,
    FulfillmentExecutionStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
    ProductStatus,
    ShipmentStatus,
    SubscriptionProvider,
    SubscriptionStatus,
    SyncResource,
)

# ---------------------------------------------------------------------------
# Identifier types — typed ints so mypy catches accidental cross-aggregate use.
# ---------------------------------------------------------------------------

StoreId = NewType("StoreId", int)
LocationId = NewType("LocationId", int)
CustomerId = NewType("CustomerId", int)
ProductId = NewType("ProductId", int)
VariantId = NewType("VariantId", int)
InventoryItemId = NewType("InventoryItemId", int)
InventoryLevelId = NewType("InventoryLevelId", int)
OrderId = NewType("OrderId", int)
OrderLineItemId = NewType("OrderLineItemId", int)
FulfillmentId = NewType("FulfillmentId", int)
SubscriptionContractId = NewType("SubscriptionContractId", int)
RefundId = NewType("RefundId", int)
ApiTokenId = NewType("ApiTokenId", int)
ApiAuditLogId = NewType("ApiAuditLogId", int)

Money = Decimal


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Stores & locations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Store:
    id: StoreId
    store_key: str
    shop_domain: str
    display_name: str
    plus: bool
    subscription_provider: SubscriptionProvider
    read_only: bool
    active: bool
    timezone: str | None
    currency_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class Location:
    id: LocationId
    store_id: StoreId
    gid: str
    legacy_id: int
    name: str
    address1: str | None
    address2: str | None
    city: str | None
    province: str | None
    postal_code: str | None
    country: str | None
    is_active: bool
    fulfills_online_orders: bool
    ships_inventory: bool
    last_seen_at: datetime


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Customer:
    id: CustomerId
    store_id: StoreId
    gid: str
    legacy_id: int
    email: str | None
    phone: str | None
    first_name: str | None
    last_name: str | None
    accepts_marketing: bool
    orders_count: int
    total_spent: Money
    currency_code: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Catalog (Product aggregate: Product + Variant + InventoryItem reference)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Variant:
    id: VariantId
    store_id: StoreId
    product_id: ProductId
    gid: str
    legacy_id: int
    title: str
    sku: str | None
    barcode: str | None
    position: int | None
    price: Money
    compare_at_price: Money | None
    currency_code: str | None
    inventory_item_id: InventoryItemId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Product:
    id: ProductId
    store_id: StoreId
    gid: str
    legacy_id: int
    title: str
    handle: str
    status: ProductStatus
    vendor: str | None
    product_type: str | None
    tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    variants: tuple[Variant, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True, kw_only=True)
class InventoryItem:
    id: InventoryItemId
    store_id: StoreId
    variant_id: VariantId | None
    gid: str
    legacy_id: int
    sku: str | None
    tracked: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class InventoryLevel:
    id: InventoryLevelId
    store_id: StoreId
    inventory_item_id: InventoryItemId
    location_id: LocationId
    available: int | None
    on_hand: int | None
    committed: int | None
    incoming: int | None
    updated_at: datetime


# ---------------------------------------------------------------------------
# Orders (aggregate: Order + LineItems + ShippingAddress + Fulfillments)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderLineItem:
    id: OrderLineItemId
    order_id: OrderId
    store_id: StoreId
    variant_id: VariantId | None
    product_id: ProductId | None
    gid: str | None
    legacy_id: int | None
    title: str
    sku: str | None
    vendor: str | None
    quantity: int
    price: Money
    total_discount: Money
    fulfillment_status: OrderLineFulfillmentStatus | None
    requires_shipping: bool
    taxable: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderShippingAddress:
    order_id: OrderId
    store_id: StoreId
    name: str | None
    company: str | None
    address1: str | None
    address2: str | None
    city: str | None
    province: str | None
    country: str | None
    zip: str | None
    phone: str | None
    latitude: Decimal | None
    longitude: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Fulfillment:
    id: FulfillmentId
    order_id: OrderId
    store_id: StoreId
    location_id: LocationId | None
    gid: str
    legacy_id: int
    status: FulfillmentExecutionStatus
    shipment_status: ShipmentStatus | None
    tracking_company: str | None
    tracking_number: str | None
    tracking_url: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class Order:
    id: OrderId
    store_id: StoreId
    customer_id: CustomerId | None
    gid: str
    legacy_id: int
    name: str
    order_number: int | None
    email: str | None
    financial_status: FinancialStatus | None
    fulfillment_status: FulfillmentStatus | None
    currency_code: str
    presentment_currency_code: str | None
    subtotal_price: Money
    total_price: Money
    total_tax: Money
    total_discounts: Money
    total_shipping: Money
    presentment_subtotal_price: Money | None
    presentment_total_price: Money | None
    processed_at: datetime
    cancelled_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    line_items: tuple[OrderLineItem, ...] = field(default_factory=tuple)
    shipping_address: OrderShippingAddress | None = None
    fulfillments: tuple[Fulfillment, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SubscriptionContract:
    id: SubscriptionContractId
    store_id: StoreId
    customer_id: CustomerId | None
    provider: SubscriptionProvider
    provider_contract_id: str
    gid: str | None
    legacy_id: int | None
    status: SubscriptionStatus
    next_billing_date: datetime | None
    frequency_interval: str | None
    frequency_count: int | None
    currency_code: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionsDay:
    store_id: StoreId
    date: date
    sessions: int | None
    orders: int | None
    total_sales: Money | None
    units_sold: int | None
    source: AnalyticsSource
    pulled_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalyticsKpiDay:
    store_id: StoreId
    date: date
    sessions: int | None
    orders: int | None
    units: int | None
    revenue: Money | None
    conversion_rate: Decimal | None
    aov: Money | None
    computed_at: datetime


# ---------------------------------------------------------------------------
# Sync state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncStateRow:
    store_id: StoreId
    resource: SyncResource
    last_completed_at: datetime | None
    last_cursor: str | None
    last_error: str | None
    last_error_at: datetime | None
    updated_at: datetime


# ---------------------------------------------------------------------------
# Refunds (separate from Order — pulled by a dedicated sync, not bulk)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Refund:
    """One Shopify refund. Multiple refunds can exist per order (partial refunds).

    `created_at` is the Shopify timestamp — used by reporting to deduct
    refunds in the window they happened (independent of the order's
    `processed_at`).
    """

    id: RefundId
    store_id: StoreId
    order_id: OrderId
    gid: str
    legacy_id: int
    amount: Money
    currency_code: str
    note: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Order aggregate / cross-store comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderAggregate:
    """Per-store rollup over a `[since, until]` window.

    Semantics:
      - `count` is every order in the window regardless of status.
      - `revenue` and `units` count only orders with financial_status='paid'
        (matches "money received" — pending and refunded don't contribute).
      - `status_counts` lets the caller reconstruct other variants
        (e.g. count-paid, count-refunded) without a round-trip.
      - `currency_code` is the dominant currency among matched orders;
        for cross-store reports the caller compares per-row currencies and
        surfaces a warning if they differ.
    """

    store_id: StoreId
    since: datetime
    until: datetime
    count: int
    revenue: Money
    units: int
    currency_code: str | None
    status_counts: dict[FinancialStatus, int]


# ---------------------------------------------------------------------------
# API auth + audit (TR-4, TR-6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ApiToken:
    """Stored bearer-token record. `token_hash` is SHA-256 of the plaintext."""

    id: ApiTokenId
    name: str
    token_hash: str
    store_id: StoreId | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ApiAuditLogEntry:
    """One inbound API or MCP call. Append-only."""

    id: ApiAuditLogId
    ts: datetime
    caller_identity: str
    store_id: StoreId | None
    surface: str  # ApiSurface value
    route_or_tool: str
    params_sanitized: dict[str, object] | None
    status_code: int | None
    latency_ms: int | None
    request_id: str | None
