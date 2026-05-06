"""SyncService — orchestrates bulk and on-demand pulls from Shopify into Postgres.

This is what `flask sync init` drives. The two flagship methods:

- `sync_orders(store_key, since=...)` runs a Bulk Operation for orders
  modified after `since`, parses the JSONL output, and upserts each order
  with its customer + line items + shipping address. Fulfillments are
  delivered separately via webhooks.

- `sync_locations(store_key)` uses a regular paginated GraphQL query
  (locations are small per Phase 0 — typically ≤3 per store) and upserts
  every location.

Each successful run records a `sync_state` row so the next call can use
`since=last_completed_at` for incremental syncs (TR-15 nightly reconcile).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import SyncResource
from app.domain.models import Location, LocationId, StoreId, SyncStateRow
from app.domain.repositories import UnitOfWork
from app.services._store_resolver import ensure_store
from app.shopify.bulk import BulkOperationsClient
from app.shopify.client import ShopifyClient
from app.shopify.config import StoreConfig
from app.shopify.jsonl import group_bulk_jsonl
from app.shopify.normalizers.customers_bulk import normalize_customer_bulk
from app.shopify.normalizers.inventory_paginated import normalize_inventory_item
from app.shopify.normalizers.orders_bulk import normalize_order_bulk
from app.shopify.normalizers.products_bulk import normalize_product_bulk

_ORDER_BULK_QUERY_TEMPLATE = """
{{
  orders(query: "{filter}", sortKey: UPDATED_AT) {{
    edges {{
      node {{
        id
        legacyResourceId
        name
        email
        phone
        processedAt
        createdAt
        updatedAt
        cancelledAt
        closedAt
        currencyCode
        presentmentCurrencyCode
        displayFinancialStatus
        displayFulfillmentStatus
        subtotalPriceSet {{ shopMoney {{ amount }} presentmentMoney {{ amount }} }}
        totalPriceSet {{ shopMoney {{ amount }} presentmentMoney {{ amount }} }}
        totalTaxSet {{ shopMoney {{ amount }} }}
        totalDiscountsSet {{ shopMoney {{ amount }} }}
        totalShippingPriceSet {{ shopMoney {{ amount }} }}
        customer {{
          id
          legacyResourceId
          email
          phone
          firstName
          lastName
          createdAt
          updatedAt
          numberOfOrders
          amountSpent {{ amount currencyCode }}
        }}
        shippingAddress {{
          name
          firstName
          lastName
          company
          address1
          address2
          city
          province
          country
          zip
          phone
          latitude
          longitude
        }}
        lineItems {{
          edges {{
            node {{
              id
              title
              sku
              vendor
              quantity
              variant {{ id }}
              product {{ id }}
              originalUnitPriceSet {{ shopMoney {{ amount }} }}
              totalDiscountSet {{ shopMoney {{ amount }} }}
              requiresShipping
              taxable
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

_CUSTOMER_BULK_QUERY_TEMPLATE = """
{{
  customers(query: "{filter}", sortKey: UPDATED_AT) {{
    edges {{
      node {{
        id
        legacyResourceId
        email
        phone
        firstName
        lastName
        createdAt
        updatedAt
        numberOfOrders
        amountSpent {{ amount currencyCode }}
        emailMarketingConsent {{ marketingState }}
      }}
    }}
  }}
}}
"""

_PRODUCT_BULK_QUERY_TEMPLATE = """
{{
  products(query: "{filter}", sortKey: UPDATED_AT) {{
    edges {{
      node {{
        id
        legacyResourceId
        title
        handle
        status
        vendor
        productType
        tags
        createdAt
        updatedAt
        variants {{
          edges {{
            node {{
              id
              legacyResourceId
              title
              sku
              barcode
              position
              price
              compareAtPrice
              inventoryItem {{ id }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

_INVENTORY_QUERY = """
query Inventory($cursor: String) {
  inventoryItems(first: 100, after: $cursor) {
    edges {
      cursor
      node {
        id
        legacyResourceId
        sku
        tracked
        variant { id }
        inventoryLevels(first: 50) {
          edges {
            node {
              id
              location { id }
              quantities(names: ["available", "on_hand", "committed", "incoming"]) {
                name
                quantity
              }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage }
  }
}
"""

_LOCATIONS_QUERY = """
query Locations($cursor: String) {
  locations(first: 250, after: $cursor, includeInactive: true) {
    edges {
      cursor
      node {
        id
        legacyResourceId
        name
        isActive
        fulfillsOnlineOrders
        shipsInventory
        address {
          address1
          address2
          city
          province
          zip
          country
        }
      }
    }
    pageInfo { hasNextPage }
  }
}
"""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SyncResult:
    store_key: str
    resource: SyncResource
    upserted: int


class SyncService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        shopify_client: ShopifyClient,
        bulk_client: BulkOperationsClient,
        store_configs: dict[str, StoreConfig],
    ) -> None:
        self._uow_factory = uow_factory
        self._client = shopify_client
        self._bulk_client = bulk_client
        self._configs = store_configs

    # -----------------------------------------------------------------------
    # Orders (Bulk)
    # -----------------------------------------------------------------------

    def sync_orders(
        self,
        store_key: str,
        *,
        since: datetime | None = None,
        max_wait_seconds: int = 1800,
    ) -> SyncResult:
        cfg = self._configs[store_key]
        store_id = self._resolve_store_id(cfg)

        bulk_query = _ORDER_BULK_QUERY_TEMPLATE.format(filter=_format_since_filter(since))

        # Pre-resolve catalog GID→id maps once so line items resolve their FKs
        # without per-line DB roundtrips. Empty maps (no products synced yet)
        # mean variant_id/product_id stay None — the next reconcile picks up.
        with self._uow_factory() as uow:
            variants_by_gid = uow.products.variant_gid_map(store_id)
            products_by_gid = uow.products.product_gid_map(store_id)

        # Stream lines into memory — v1 volume is bounded enough.
        lines = list(
            self._bulk_client.run_and_collect(
                store_key, bulk_query, max_wait_seconds=max_wait_seconds
            )
        )
        grouped = group_bulk_jsonl(lines, {"LineItem": "line_items"})

        count = 0
        for order_dict in grouped.values():
            normalized = normalize_order_bulk(
                store_id,
                order_dict,
                variants_by_gid=variants_by_gid,
                products_by_gid=products_by_gid,
            )
            with self._uow_factory() as uow:
                customer_id = None
                if normalized.customer is not None:
                    uow.customers.upsert(normalized.customer)
                    cust = uow.customers.get_by_gid(store_id, normalized.customer.gid)
                    if cust is not None:
                        customer_id = cust.id
                order = dataclasses.replace(normalized.order, customer_id=customer_id)
                uow.orders.upsert(order)
                uow.commit()
            count += 1

        self._mark_sync_complete(store_id, SyncResource.ORDERS)
        return SyncResult(store_key=store_key, resource=SyncResource.ORDERS, upserted=count)

    # -----------------------------------------------------------------------
    # Customers (Bulk)
    # -----------------------------------------------------------------------

    def sync_customers(
        self,
        store_key: str,
        *,
        since: datetime | None = None,
        max_wait_seconds: int = 1800,
    ) -> SyncResult:
        cfg = self._configs[store_key]
        store_id = self._resolve_store_id(cfg)

        bulk_query = _CUSTOMER_BULK_QUERY_TEMPLATE.format(filter=_format_since_filter(since))

        lines = list(
            self._bulk_client.run_and_collect(
                store_key, bulk_query, max_wait_seconds=max_wait_seconds
            )
        )
        # No children to attach — every line is a root Customer.
        grouped = group_bulk_jsonl(lines, {})

        count = 0
        for cust_dict in grouped.values():
            customer = normalize_customer_bulk(store_id, cust_dict)
            with self._uow_factory() as uow:
                uow.customers.upsert(customer)
                uow.commit()
            count += 1

        self._mark_sync_complete(store_id, SyncResource.CUSTOMERS)
        return SyncResult(store_key=store_key, resource=SyncResource.CUSTOMERS, upserted=count)

    # -----------------------------------------------------------------------
    # Products (Bulk: Product + ProductVariant)
    # -----------------------------------------------------------------------

    def sync_products(
        self,
        store_key: str,
        *,
        since: datetime | None = None,
        max_wait_seconds: int = 1800,
    ) -> SyncResult:
        cfg = self._configs[store_key]
        store_id = self._resolve_store_id(cfg)

        bulk_query = _PRODUCT_BULK_QUERY_TEMPLATE.format(filter=_format_since_filter(since))

        lines = list(
            self._bulk_client.run_and_collect(
                store_key, bulk_query, max_wait_seconds=max_wait_seconds
            )
        )
        grouped = group_bulk_jsonl(lines, {"ProductVariant": "variants"})

        count = 0
        for prod_dict in grouped.values():
            product = normalize_product_bulk(store_id, prod_dict)
            with self._uow_factory() as uow:
                uow.products.upsert(product)
                uow.commit()
            count += 1

        self._mark_sync_complete(store_id, SyncResource.PRODUCTS)
        return SyncResult(store_key=store_key, resource=SyncResource.PRODUCTS, upserted=count)

    # -----------------------------------------------------------------------
    # Locations (paginated regular query)
    # -----------------------------------------------------------------------

    def sync_locations(self, store_key: str) -> SyncResult:
        cfg = self._configs[store_key]
        store_id = self._resolve_store_id(cfg)

        cursor: str | None = None
        count = 0
        now = datetime.now(tz=UTC)
        while True:
            data = self._client.query(store_key, _LOCATIONS_QUERY, variables={"cursor": cursor})
            conn = data.get("locations") or {}
            edges = conn.get("edges") or []
            for edge in edges:
                node = edge.get("node") or {}
                location = _normalize_location(store_id, node, now)
                with self._uow_factory() as uow:
                    uow.locations.upsert(location)
                    uow.commit()
                count += 1
            page_info = conn.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = edges[-1].get("cursor") if edges else None
            if cursor is None:
                break

        self._mark_sync_complete(store_id, SyncResource.LOCATIONS)
        return SyncResult(store_key=store_key, resource=SyncResource.LOCATIONS, upserted=count)

    # -----------------------------------------------------------------------
    # Inventory (paginated regular query — bulk can't ship `quantities`)
    # -----------------------------------------------------------------------

    def sync_inventory(self, store_key: str) -> SyncResult:
        cfg = self._configs[store_key]
        store_id = self._resolve_store_id(cfg)

        # Pre-resolve the GID lookup tables once. Re-running through them
        # in-memory beats per-item DB roundtrips for FK resolution.
        with self._uow_factory() as uow:
            variants_by_gid = uow.products.variant_gid_map(store_id)
            locations_by_gid = {loc.gid: loc.id for loc in uow.locations.list_for_store(store_id)}

        cursor: str | None = None
        count = 0
        while True:
            data = self._client.query(store_key, _INVENTORY_QUERY, variables={"cursor": cursor})
            conn = data.get("inventoryItems") or {}
            edges = conn.get("edges") or []
            for edge in edges:
                node = edge.get("node") or {}
                normalized = normalize_inventory_item(
                    store_id,
                    node,
                    variants_by_gid=variants_by_gid,
                    locations_by_gid=locations_by_gid,
                )
                with self._uow_factory() as uow:
                    uow.inventory.upsert_item(normalized.item)
                    persisted = uow.inventory.get_item(store_id, normalized.item.gid)
                    if persisted is not None:
                        for level in normalized.levels:
                            uow.inventory.upsert_level(
                                dataclasses.replace(level, inventory_item_id=persisted.id)
                            )
                    uow.commit()
                count += 1
            page_info = conn.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = edges[-1].get("cursor") if edges else None
            if cursor is None:
                break

        self._mark_sync_complete(store_id, SyncResource.INVENTORY)
        return SyncResult(store_key=store_key, resource=SyncResource.INVENTORY, upserted=count)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _resolve_store_id(self, cfg: StoreConfig) -> StoreId:
        with self._uow_factory() as uow:
            store_id = ensure_store(uow, cfg)
            uow.commit()
            return store_id

    def _mark_sync_complete(self, store_id: StoreId, resource: SyncResource) -> None:
        now = datetime.now(tz=UTC)
        row = SyncStateRow(
            store_id=store_id,
            resource=resource,
            last_completed_at=now,
            last_cursor=None,
            last_error=None,
            last_error_at=None,
            updated_at=now,
        )
        with self._uow_factory() as uow:
            uow.sync_state.upsert(row)
            uow.commit()


def _format_since_filter(since: datetime | None) -> str:
    if since is None:
        return ""
    # Shopify's `query:` filter accepts ISO 8601 with a trailing Z or offset.
    # `updated_at:>=2026-04-29T00:00:00Z` form is the simplest reliable shape.
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    iso = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"updated_at:>={iso}"


def _normalize_location(store_id: StoreId, payload: dict[str, Any], now: datetime) -> Location:
    address = payload.get("address") or {}
    legacy_raw = payload.get("legacyResourceId")
    return Location(
        id=LocationId(0),
        store_id=store_id,
        gid=str(payload["id"]),
        legacy_id=int(legacy_raw) if legacy_raw is not None else 0,
        name=str(payload.get("name") or ""),
        address1=address.get("address1"),
        address2=address.get("address2"),
        city=address.get("city"),
        province=address.get("province"),
        postal_code=address.get("zip"),
        country=address.get("country"),
        is_active=bool(payload.get("isActive", True)),
        fulfills_online_orders=bool(payload.get("fulfillsOnlineOrders", False)),
        ships_inventory=bool(payload.get("shipsInventory", False)),
        last_seen_at=now,
    )
