"""Filter specs for repository queries (design 11A.6).

Use a Spec when a `find_*` method would otherwise need more than ~3
optional kwargs. Specs are immutable, type-checked, and let new filters
land without breaking existing call sites.

Repositories accept a Spec by value; callers may construct one with
`dataclasses.replace(spec, financial_status=...)` to derive variants.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.domain.enums import (
    FinancialStatus,
    FulfillmentStatus,
    ProductStatus,
    SubscriptionProvider,
    SubscriptionStatus,
)
from app.domain.models import (
    CustomerId,
    LocationId,
    StoreId,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderSpec:
    """Filter for Order list/search.

    `store_ids=None` means "all stores" — cross-store queries are first-class.
    """

    store_ids: tuple[StoreId, ...] | None = None
    since: datetime | None = None
    until: datetime | None = None
    financial_status: FinancialStatus | None = None
    fulfillment_status: FulfillmentStatus | None = None
    sku: str | None = None
    customer_id: CustomerId | None = None
    customer_email: str | None = None
    min_total: Decimal | None = None
    tag: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductSpec:
    store_ids: tuple[StoreId, ...] | None = None
    status: ProductStatus | None = None
    title_query: str | None = None
    handle: str | None = None
    vendor: str | None = None
    product_type: str | None = None
    tag: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class InventorySpec:
    """Filter for inventory_levels lookups.

    `low_stock_threshold` filters to levels where `available < threshold` —
    drives the low-stock dashboard view and the `list_low_stock` MCP tool.
    """

    store_ids: tuple[StoreId, ...] | None = None
    location_id: LocationId | None = None
    sku: str | None = None
    low_stock_threshold: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SubscriptionSpec:
    store_ids: tuple[StoreId, ...] | None = None
    customer_id: CustomerId | None = None
    status: SubscriptionStatus | None = None
    provider: SubscriptionProvider | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalyticsWindowSpec:
    """Window for analytics_kpi_daily / sessions_daily lookups."""

    store_ids: tuple[StoreId, ...] | None = None
    since: date
    until: date
