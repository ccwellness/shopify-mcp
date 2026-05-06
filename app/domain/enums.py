"""Domain enums.

Values are lowercase strings — matches the migration's server_defaults
(`'received'`, `'shopifyql'`, `'unknown'`) and is what we persist to the DB.
Shopify GraphQL emits SCREAMING_SNAKE_CASE; normalization happens once at
ingest in `app/shopify/normalizers.py` so everything downstream sees a
single shape.
"""

from __future__ import annotations

from enum import StrEnum


class FinancialStatus(StrEnum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    VOIDED = "voided"
    EXPIRED = "expired"


class FulfillmentStatus(StrEnum):
    """Order-level fulfillment status. `null` from Shopify is mapped to UNFULFILLED at ingest."""

    UNFULFILLED = "unfulfilled"
    PARTIAL = "partial"
    FULFILLED = "fulfilled"
    RESTOCKED = "restocked"


class OrderLineFulfillmentStatus(StrEnum):
    """Line-item-level fulfillment status. Distinct from order-level — Shopify exposes both."""

    UNFULFILLED = "unfulfilled"
    PARTIAL = "partial"
    FULFILLED = "fulfilled"
    NOT_ELIGIBLE = "not_eligible"


class FulfillmentExecutionStatus(StrEnum):
    """`fulfillments.status` from the Shopify Fulfillment object — execution lifecycle."""

    PENDING = "pending"
    OPEN = "open"
    SUCCESS = "success"
    CANCELLED = "cancelled"
    ERROR = "error"
    FAILURE = "failure"


class ShipmentStatus(StrEnum):
    """`fulfillments.shipment_status` — carrier tracking lifecycle."""

    LABEL_PRINTED = "label_printed"
    LABEL_PURCHASED = "label_purchased"
    ATTEMPTED_DELIVERY = "attempted_delivery"
    READY_FOR_PICKUP = "ready_for_pickup"
    CONFIRMED = "confirmed"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    FAILURE = "failure"


class ProductStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DRAFT = "draft"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class SubscriptionProvider(StrEnum):
    """Which subscription stack writes the row. Phase 0 confirmed OrderGroove on all stores."""

    NATIVE = "native"
    ORDERGROOVE = "ordergroove"
    UNKNOWN = "unknown"


class AnalyticsSource(StrEnum):
    """Where a `sessions_daily` row came from. TR-30: GA4 fallback if ShopifyQL probe fails."""

    SHOPIFYQL = "shopifyql"
    GA4 = "ga4"


class SyncResource(StrEnum):
    """`sync_state.resource` — one row per (store, resource) tracking last successful sync."""

    ORDERS = "orders"
    PRODUCTS = "products"
    VARIANTS = "variants"
    INVENTORY = "inventory"
    CUSTOMERS = "customers"
    FULFILLMENTS = "fulfillments"
    LOCATIONS = "locations"
    SESSIONS = "sessions"
    SUBSCRIPTIONS = "subscriptions"


class WebhookProcessingStatus(StrEnum):
    """`webhook_events_log.processing_status` — pipeline state for one delivery."""

    RECEIVED = "received"
    ENQUEUED = "enqueued"
    PROCESSED = "processed"
    FAILED = "failed"


class ApiSurface(StrEnum):
    """`api_audit_log.surface` — which presentation layer fielded the call."""

    REST = "rest"
    GRAPHQL = "graphql"
    MCP = "mcp"
