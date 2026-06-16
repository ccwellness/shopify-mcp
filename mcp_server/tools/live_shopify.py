"""Live Shopify GraphQL MCP tools — bypass the local database.

These tools call the Shopify Admin API directly and return raw GraphQL
payloads, for callers who don't want to wait for (or rely on) a sync into
Postgres. The DB-backed tools (`list_orders`, `get_product`, etc.) stay
authoritative for cross-store joins and analytics; these are the escape
hatch for ad-hoc lookups and freshness-critical reads.

Read-only enforcement still applies: the underlying `ShopifyClient`
refuses mutations against stores marked `read_only=True` (TR-46), so the
`shopify_live_graphql` escape hatch can't be used to write.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.shopify.errors import ShopifyError
from mcp_server.audit import audited
from mcp_server.dates import DateParseError, parse_datetime
from mcp_server.server import mcp, services

_MAX_LIMIT = 50

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------
#
# These are intentionally smaller than the bulk-sync queries — they target
# the "give me this one thing now" use case, not full-fidelity replication.
# If a caller needs a field that's not here, they can use the
# `shopify_live_graphql` escape hatch.

_GET_ORDER_QUERY = """
query LiveOrder($id: ID!) {
  order(id: $id) {
    id legacyResourceId name email phone
    processedAt createdAt updatedAt cancelledAt closedAt
    currencyCode presentmentCurrencyCode sourceName
    displayFinancialStatus displayFulfillmentStatus
    subtotalPriceSet { shopMoney { amount currencyCode } }
    totalPriceSet { shopMoney { amount currencyCode } }
    totalTaxSet { shopMoney { amount } }
    totalDiscountsSet { shopMoney { amount } }
    totalShippingPriceSet { shopMoney { amount } }
    customer { id legacyResourceId email firstName lastName }
    shippingAddress { name address1 address2 city province country zip phone }
    lineItems(first: 100) {
      edges {
        node {
          id title sku vendor quantity
          variant { id } product { id }
          originalUnitPriceSet { shopMoney { amount } }
          totalDiscountSet { shopMoney { amount } }
        }
      }
    }
  }
}
"""

_LIST_ORDERS_QUERY = """
query LiveOrders($query: String, $first: Int!, $after: String) {
  orders(first: $first, after: $after, query: $query, sortKey: PROCESSED_AT, reverse: true) {
    edges {
      cursor
      node {
        id legacyResourceId name email
        processedAt createdAt updatedAt
        currencyCode sourceName
        displayFinancialStatus displayFulfillmentStatus
        totalPriceSet { shopMoney { amount currencyCode } }
        customer { id email firstName lastName }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_GET_PRODUCT_QUERY = """
query LiveProduct($id: ID!) {
  product(id: $id) {
    id legacyResourceId title handle status vendor productType tags
    createdAt updatedAt
    variants(first: 100) {
      edges {
        node {
          id legacyResourceId title sku barcode position
          price compareAtPrice
          inventoryItem { id legacyResourceId sku tracked }
        }
      }
    }
  }
}
"""

_LIST_PRODUCTS_QUERY = """
query LiveProducts($query: String, $first: Int!, $after: String) {
  products(first: $first, after: $after, query: $query, sortKey: UPDATED_AT, reverse: true) {
    edges {
      cursor
      node {
        id legacyResourceId title handle status vendor productType tags
        createdAt updatedAt
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# productVariants supports `sku:` as a query qualifier; one SKU may map to
# several variants across products (rare but legal).
_INVENTORY_BY_SKU_QUERY = """
query LiveInventoryBySku($query: String!, $first: Int!) {
  productVariants(first: $first, query: $query) {
    edges {
      node {
        id legacyResourceId sku title
        product { id title handle status }
        inventoryItem {
          id legacyResourceId sku tracked
          inventoryLevels(first: 50) {
            edges {
              node {
                id
                location { id name }
                quantities(names: ["available", "on_hand", "committed", "incoming"]) {
                  name quantity
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class LiveOrderOut(BaseModel):
    """Raw GraphQL Order node, or null if not found. Money is a nested object
    `{ shopMoney: { amount, currencyCode } }` — strings, not floats."""

    store_key: str
    order: dict[str, Any] | None


class LiveOrderListOut(BaseModel):
    """One page of raw GraphQL Order nodes plus an opaque next_cursor."""

    store_key: str
    items: list[dict[str, Any]]
    next_cursor: str | None


class LiveProductOut(BaseModel):
    """Raw GraphQL Product node, or null if not found."""

    store_key: str
    product: dict[str, Any] | None


class LiveProductListOut(BaseModel):
    """One page of raw GraphQL Product nodes plus an opaque next_cursor."""

    store_key: str
    items: list[dict[str, Any]]
    next_cursor: str | None


class LiveInventoryOut(BaseModel):
    """All variants matching `sku` with their per-location inventory levels."""

    store_key: str
    sku: str
    variants: list[dict[str, Any]]


class LiveGraphQLOut(BaseModel):
    """Raw `data` block from the GraphQL response."""

    store_key: str
    data: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_store(store_key: str) -> None:
    if store_key not in services().store_configs:
        raise ValueError(f"unknown store_key: {store_key!r}")


def _build_orders_filter(
    since_iso: str | None,
    until_iso: str | None,
    financial_status: str | None,
    sku: str | None,
) -> str | None:
    """Compose a Shopify orders search-query string from MCP filter args.

    Shopify search syntax: terms are space-separated; values containing
    spaces or punctuation must be quoted. We escape double quotes in
    user-provided values defensively.
    """
    parts: list[str] = []
    if since_iso:
        parts.append(f"updated_at:>={since_iso}")
    if until_iso:
        parts.append(f"updated_at:<={until_iso}")
    if financial_status:
        parts.append(f"financial_status:{financial_status}")
    if sku:
        parts.append(f'sku:"{sku.replace(chr(34), "")}"')
    return " ".join(parts) if parts else None


def _build_products_filter(
    status: str | None,
    title_query: str | None,
    vendor: str | None,
    product_type: str | None,
    tag: str | None,
) -> str | None:
    parts: list[str] = []
    if status:
        parts.append(f"status:{status}")
    if title_query:
        parts.append(f"title:*{title_query.replace(chr(34), '')}*")
    if vendor:
        parts.append(f'vendor:"{vendor.replace(chr(34), "")}"')
    if product_type:
        parts.append(f'product_type:"{product_type.replace(chr(34), "")}"')
    if tag:
        parts.append(f'tag:"{tag.replace(chr(34), "")}"')
    return " ".join(parts) if parts else None


def _flatten_edges(connection: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str | None]:
    """Take a `{edges: [{cursor, node}], pageInfo: {...}}` and return
    `(nodes, end_cursor_or_None)`."""
    if not connection:
        return [], None
    edges = connection.get("edges") or []
    nodes = [e["node"] for e in edges if isinstance(e, dict) and e.get("node")]
    page_info = connection.get("pageInfo") or {}
    end_cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
    return nodes, end_cursor if isinstance(end_cursor, str) else None


# ---------------------------------------------------------------------------
# Tools — orders
# ---------------------------------------------------------------------------


@mcp.tool
@audited("shopify_live_get_order")
def shopify_live_get_order(
    store_key: str = Field(description="Store key, e.g. 'lubelife' or 'shopjo'."),
    order_gid: str = Field(
        description="Full Shopify order GID, e.g. 'gid://shopify/Order/7117040550127'.",
    ),
) -> LiveOrderOut:
    """Fetch one order LIVE from Shopify — bypasses the local DB.

    Use this when you need the freshest possible state for a single order
    (just-placed, just-refunded, status flapping) and don't want to wait
    for a sync. Returns the raw GraphQL `Order` node — money fields are
    `{ shopMoney: { amount: '12.34', currencyCode: 'USD' } }` strings.
    """
    _require_store(store_key)
    data = services().shopify.query(store_key, _GET_ORDER_QUERY, {"id": order_gid})
    return LiveOrderOut(store_key=store_key, order=data.get("order"))


@mcp.tool
@audited("shopify_live_list_orders")
def shopify_live_list_orders(  # noqa: PLR0913 — flat filter args mirror REST + GraphQL
    store_key: str = Field(description="Store key, e.g. 'lubelife' or 'shopjo'."),
    since: str | None = Field(
        default=None,
        description=(
            "Filter to orders with updated_at >= this timestamp. "
            "ISO 8601 or relative ('yesterday', '7d', 'last_week')."
        ),
    ),
    until: str | None = Field(
        default=None,
        description="Filter to orders with updated_at <= this timestamp.",
    ),
    financial_status: str | None = Field(
        default=None,
        description=(
            "Shopify financial_status filter (paid, pending, authorized, "
            "refunded, partially_refunded, voided, expired, partially_paid)."
        ),
    ),
    sku: str | None = Field(default=None, description="Restrict to orders containing this SKU."),
    limit: int = Field(default=50, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Field(
        default=None,
        description="Opaque next_cursor from a prior page (Shopify endCursor).",
    ),
) -> LiveOrderListOut:
    """List orders LIVE from Shopify, newest processed_at first.

    Filters compose into a Shopify search query. Mirrors the search syntax
    you'd type into the Shopify Admin order list. Use the DB-backed
    `list_orders` instead when you need to join across stores or paginate
    deep history — Shopify's API throttles harder than Postgres does.
    """
    _require_store(store_key)
    try:
        since_dt = parse_datetime(since)
        until_dt = parse_datetime(until)
    except DateParseError as exc:
        raise ValueError(str(exc)) from exc

    query_string = _build_orders_filter(
        since_iso=since_dt.isoformat() if since_dt else None,
        until_iso=until_dt.isoformat() if until_dt else None,
        financial_status=financial_status or None,
        sku=sku or None,
    )
    data = services().shopify.query(
        store_key,
        _LIST_ORDERS_QUERY,
        {"query": query_string, "first": limit, "after": cursor},
    )
    nodes, next_cursor = _flatten_edges(data.get("orders"))
    return LiveOrderListOut(store_key=store_key, items=nodes, next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# Tools — products
# ---------------------------------------------------------------------------


@mcp.tool
@audited("shopify_live_get_product")
def shopify_live_get_product(
    store_key: str = Field(description="Store key, e.g. 'lubelife' or 'shopjo'."),
    product_gid: str = Field(
        description="Full Shopify product GID, e.g. 'gid://shopify/Product/123456'.",
    ),
) -> LiveProductOut:
    """Fetch one product LIVE from Shopify — bypasses the local DB.

    Returns the product node with all variants and their inventory item
    refs. To get inventory levels per location, follow up with
    `shopify_live_inventory_by_sku`.
    """
    _require_store(store_key)
    data = services().shopify.query(store_key, _GET_PRODUCT_QUERY, {"id": product_gid})
    return LiveProductOut(store_key=store_key, product=data.get("product"))


@mcp.tool
@audited("shopify_live_list_products")
def shopify_live_list_products(  # noqa: PLR0913 — flat filter args mirror REST + GraphQL
    store_key: str = Field(description="Store key, e.g. 'lubelife' or 'shopjo'."),
    status: str | None = Field(default=None, description="One of: active, archived, draft."),
    title_query: str | None = Field(
        default=None, description="Substring match on the product title."
    ),
    vendor: str | None = Field(default=None, description="Exact-match vendor."),
    product_type: str | None = Field(default=None, description="Exact-match product type."),
    tag: str | None = Field(default=None, description="Filter to products carrying this tag."),
    limit: int = Field(default=50, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Field(default=None, description="Opaque next_cursor from a prior page."),
) -> LiveProductListOut:
    """List products LIVE from Shopify, most-recently-updated first.

    Use `shopify_live_get_product` after picking an id from this list.
    """
    _require_store(store_key)
    query_string = _build_products_filter(
        status=status or None,
        title_query=title_query or None,
        vendor=vendor or None,
        product_type=product_type or None,
        tag=tag or None,
    )
    data = services().shopify.query(
        store_key,
        _LIST_PRODUCTS_QUERY,
        {"query": query_string, "first": limit, "after": cursor},
    )
    nodes, next_cursor = _flatten_edges(data.get("products"))
    return LiveProductListOut(store_key=store_key, items=nodes, next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# Tools — inventory
# ---------------------------------------------------------------------------


@mcp.tool
@audited("shopify_live_inventory_by_sku")
def shopify_live_inventory_by_sku(
    store_key: str = Field(description="Store key, e.g. 'lubelife' or 'shopjo'."),
    sku: str = Field(description="Exact SKU to look up."),
    limit: int = Field(
        default=10,
        ge=1,
        le=_MAX_LIMIT,
        description="Max variants to return — one SKU may map to several variants.",
    ),
) -> LiveInventoryOut:
    """Look up live inventory levels by SKU across every location.

    Returns one entry per matching variant. Each variant carries its
    `inventoryItem.inventoryLevels` connection with per-location available
    / on_hand / committed / incoming quantities (omitted by Shopify when
    not tracked).
    """
    _require_store(store_key)
    if not sku:
        raise ValueError("sku is required")
    safe_sku = sku.replace('"', "")
    data = services().shopify.query(
        store_key,
        _INVENTORY_BY_SKU_QUERY,
        {"query": f'sku:"{safe_sku}"', "first": limit},
    )
    edges = (data.get("productVariants") or {}).get("edges") or []
    variants = [e["node"] for e in edges if isinstance(e, dict) and e.get("node")]
    return LiveInventoryOut(store_key=store_key, sku=sku, variants=variants)


# ---------------------------------------------------------------------------
# Tool — generic GraphQL escape hatch
# ---------------------------------------------------------------------------


@mcp.tool
@audited("shopify_live_graphql")
def shopify_live_graphql(
    store_key: str = Field(description="Store key, e.g. 'lubelife' or 'shopjo'."),
    query: str = Field(
        description="Raw GraphQL query string. Mutations are blocked on read-only stores.",
    ),
    variables: dict[str, Any] | None = Field(  # noqa: B008
        default=None, description="Optional variables map passed to the query."
    ),
) -> LiveGraphQLOut:
    """Run an arbitrary GraphQL query against Shopify and return the `data` block.

    Escape hatch for cases the typed tools don't cover (custom selections,
    metafields, GraphQL features that aren't worth a typed wrapper).
    Mutations against read-only stores raise ReadOnlyViolation — the
    ShopifyClient enforces this regardless of the granted app scopes.
    """
    _require_store(store_key)
    if not query or not query.strip():
        raise ValueError("query is required")
    try:
        data = services().shopify.query(store_key, query, variables)
    except ShopifyError as exc:
        # Surface Shopify-specific errors as plain ValueErrors so the
        # MCP runtime returns a tool-error message the LLM can read,
        # instead of a stack trace.
        raise ValueError(str(exc)) from exc
    return LiveGraphQLOut(store_key=store_key, data=data)
