"""Live OrderRepository — reads orders from the Shopify Admin GraphQL API.

Pagination is keyset on `(processed_at desc, legacy_id desc)`, encoded with the
same opaque-cursor shape the SQLAlchemy repo uses (see `_keyset`), so the MCP
tools page identically across modes. Across multiple stores we fetch
a page from each, merge, and apply the keyset client-side — exact, if a touch
chatty. Aggregations walk every page in the window (uncapped).
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.domain.enums import FinancialStatus
from app.domain.models import (
    CustomerId,
    Order,
    OrderAggregate,
    OrderId,
    OrderLineItemId,
    Page,
    ProductId,
    ProductSalesDay,
    StoreId,
)
from app.domain.specs import OrderSpec
from app.shopify.live_paging import flatten_edges, gid_tail, iter_all_nodes
from app.shopify.normalizers.orders_bulk import normalize_order_bulk
from app.shopify.repositories import _keyset
from app.shopify.repositories._base import _LiveRepo, order_gid, product_gid

_ORDER_NODE = """
  id legacyResourceId name email
  processedAt createdAt updatedAt cancelledAt closedAt
  currencyCode presentmentCurrencyCode sourceName
  displayFinancialStatus displayFulfillmentStatus
  subtotalPriceSet { shopMoney { amount } presentmentMoney { amount } }
  totalPriceSet { shopMoney { amount } presentmentMoney { amount } }
  totalTaxSet { shopMoney { amount } }
  totalDiscountsSet { shopMoney { amount } }
  totalShippingPriceSet { shopMoney { amount } }
  customer {
    id legacyResourceId email phone firstName lastName
    numberOfOrders createdAt updatedAt
    amountSpent { amount currencyCode }
  }
  shippingAddress {
    name firstName lastName company address1 address2
    city province country zip phone latitude longitude
  }
  lineItems(first: 100) {
    edges { node {
      id title sku vendor quantity requiresShipping taxable
      variant { id legacyResourceId }
      product { id legacyResourceId }
      originalUnitPriceSet { shopMoney { amount } }
      totalDiscountSet { shopMoney { amount } }
      discountAllocations { allocatedAmountSet { shopMoney { amount } } }
    } }
  }
"""

_GET_ORDER = f"query LiveOrder($id: ID!) {{ order(id: $id) {{ {_ORDER_NODE} }} }}"

_LIST_ORDERS = f"""
query LiveOrders($query: String, $first: Int!, $after: String) {{
  orders(first: $first, after: $after, query: $query,
         sortKey: PROCESSED_AT, reverse: true) {{
    edges {{ cursor node {{ {_ORDER_NODE} }} }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

_PRODUCT_SKUS = """
query LiveProductSkus($id: ID!) {
  product(id: $id) {
    variants(first: 100) { edges { node { sku } } }
  }
}
"""


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _flatten_line_items(node: dict[str, Any]) -> dict[str, Any]:
    """Move `lineItems.edges[].node` to the flat `line_items` key the bulk
    normalizer expects. Mutates a shallow copy."""
    out = dict(node)
    edges = (out.get("lineItems") or {}).get("edges") or []
    out["line_items"] = [e["node"] for e in edges if isinstance(e, dict) and e.get("node")]
    out.pop("lineItems", None)
    return out


class LiveOrderRepository(_LiveRepo):
    # -- normalization ----------------------------------------------------

    def _to_order(self, store_id: StoreId, node: dict[str, Any]) -> Order:
        payload = _flatten_line_items(node)
        variants_by_gid: dict[str, Any] = {}
        products_by_gid: dict[str, Any] = {}
        for li in payload["line_items"]:
            variant = li.get("variant") or {}
            product = li.get("product") or {}
            v_legacy = gid_tail(variant.get("id"))
            p_legacy = gid_tail(product.get("id"))
            if variant.get("id") and v_legacy is not None:
                variants_by_gid[str(variant["id"])] = v_legacy
            if product.get("id") and p_legacy is not None:
                products_by_gid[str(product["id"])] = p_legacy

        normalized = normalize_order_bulk(
            store_id, payload, variants_by_gid=variants_by_gid, products_by_gid=products_by_gid
        )
        order = normalized.order
        new_id = OrderId(order.legacy_id)
        customer_id = (
            CustomerId(normalized.customer.legacy_id) if normalized.customer is not None else None
        )
        line_items = tuple(
            dataclasses.replace(
                li,
                id=OrderLineItemId(gid_tail(li.gid) or 0),
                order_id=new_id,
            )
            for li in order.line_items
        )
        shipping = (
            dataclasses.replace(order.shipping_address, order_id=new_id)
            if order.shipping_address is not None
            else None
        )
        return dataclasses.replace(
            order,
            id=new_id,
            customer_id=customer_id,
            line_items=line_items,
            shipping_address=shipping,
        )

    # -- single fetches ---------------------------------------------------

    def get(self, order_id: OrderId) -> Order | None:
        """Live mode has no global id space; scan stores for the legacy id."""
        for store_id in self._index.all_store_ids():
            found = self.get_by_gid(store_id, order_gid(int(order_id)))
            if found is not None:
                return found
        return None

    def get_by_gid(self, store_id: StoreId, gid: str) -> Order | None:
        key = self._key(store_id)
        if key is None:
            return None
        data = self._query(key, _GET_ORDER, {"id": gid})
        node = data.get("order")
        return self._to_order(store_id, node) if node else None

    # -- search query builder ---------------------------------------------

    def _build_query(self, spec: OrderSpec, *, watermark_ts: datetime | None) -> str | None:
        parts: list[str] = []
        if spec.since:
            parts.append(f"processed_at:>={_iso_z(spec.since)}")
        upper = spec.until
        if watermark_ts is not None and (upper is None or watermark_ts < upper):
            parts.append(f"processed_at:<={_iso_z(watermark_ts)}")
        elif upper is not None:
            parts.append(f"processed_at:<{_iso_z(upper)}")
        if spec.financial_status is not None:
            parts.append(f"financial_status:{spec.financial_status.value}")
        if spec.sku:
            parts.append(f'sku:"{spec.sku.replace(chr(34), "")}"')
        if spec.customer_email:
            parts.append(f'email:"{spec.customer_email.replace(chr(34), "")}"')
        if spec.tag:
            parts.append(f'tag:"{spec.tag.replace(chr(34), "")}"')
        return " ".join(parts) if parts else None

    def _passes_client_filters(
        self, order: Order, spec: OrderSpec, watermark: tuple[datetime, int] | None
    ) -> bool:
        if (
            spec.fulfillment_status is not None
            and order.fulfillment_status != spec.fulfillment_status
        ):
            return False
        if spec.min_total is not None and order.total_price < spec.min_total:
            return False
        if spec.customer_id is not None and order.customer_id != spec.customer_id:
            return False
        if watermark is not None:
            ts, oid = watermark
            if order.processed_at > ts or (order.processed_at == ts and order.legacy_id >= oid):
                return False
        return True

    # -- find -------------------------------------------------------------

    def find(self, spec: OrderSpec, *, limit: int = 50, cursor: str | None = None) -> Page[Order]:
        keys = self._index.resolve_keys(spec.store_ids)
        if not keys:
            return Page(items=(), next_cursor=None)

        watermark = _keyset.decode(cursor) if cursor else None
        watermark_ts = watermark[0] if watermark else None
        query = self._build_query(spec, watermark_ts=watermark_ts)

        candidates: list[Order] = []
        any_more = False
        for key in keys:
            store_id = self._index.id_for(key)
            if store_id is None:
                continue
            data = self._query(key, _LIST_ORDERS, {"query": query, "first": limit, "after": None})
            nodes, next_cursor = flatten_edges(data.get("orders"))
            any_more = any_more or next_cursor is not None
            for node in nodes:
                order = self._to_order(store_id, node)
                if self._passes_client_filters(order, spec, watermark):
                    candidates.append(order)

        candidates.sort(key=lambda o: (o.processed_at, o.legacy_id), reverse=True)
        page = candidates[:limit]
        has_more = len(candidates) > limit or (any_more and len(page) == limit)
        next_cur = (
            _keyset.encode(page[-1].processed_at, page[-1].legacy_id) if page and has_more else None
        )
        return Page(items=tuple(page), next_cursor=next_cur)

    # -- aggregations -----------------------------------------------------

    def _window_orders(self, store_id: StoreId, since: datetime, until: datetime) -> list[Order]:
        key = self._key(store_id)
        if key is None:
            return []
        query = f"processed_at:>={_iso_z(since)} processed_at:<{_iso_z(until)}"

        def _run(after: str | None) -> dict[str, Any] | None:
            data = self._query(key, _LIST_ORDERS, {"query": query, "first": 100, "after": after})
            return data.get("orders")

        return [self._to_order(store_id, node) for node in iter_all_nodes(_run)]

    def count_by_status(
        self, store_id: StoreId, since: datetime, until: datetime
    ) -> dict[FinancialStatus, int]:
        counts: Counter[FinancialStatus] = Counter()
        for order in self._window_orders(store_id, since, until):
            if order.financial_status is not None:
                counts[order.financial_status] += 1
        return dict(counts)

    def aggregate_in_window(
        self, store_id: StoreId, since: datetime, until: datetime
    ) -> OrderAggregate:
        orders = self._window_orders(store_id, since, until)
        status_counts: Counter[FinancialStatus] = Counter()
        currencies: Counter[str] = Counter()
        revenue = Decimal("0")
        units = 0
        for order in orders:
            if order.financial_status is not None:
                status_counts[order.financial_status] += 1
            currencies[order.currency_code] += 1
            if order.financial_status is FinancialStatus.PAID:
                revenue += order.total_price
                units += sum(li.quantity for li in order.line_items)
        dominant = currencies.most_common(1)[0][0] if currencies else None
        return OrderAggregate(
            store_id=store_id,
            since=since,
            until=until,
            count=len(orders),
            revenue=revenue,
            units=units,
            currency_code=dominant,
            status_counts=dict(status_counts),
        )

    # -- per-product ------------------------------------------------------

    def _product_skus(self, store_key: str, product_id: ProductId) -> list[str]:
        data = self._query(store_key, _PRODUCT_SKUS, {"id": product_gid(int(product_id))})
        product = data.get("product") or {}
        edges = (product.get("variants") or {}).get("edges") or []
        skus = {
            str(e["node"]["sku"])
            for e in edges
            if isinstance(e, dict) and (e.get("node") or {}).get("sku")
        }
        return sorted(skus)

    def _orders_with_product(
        self,
        store_id: StoreId,
        product_id: ProductId,
        *,
        since: datetime | None,
        until: datetime | None,
        limit: int | None,
    ) -> list[Order]:
        key = self._key(store_id)
        if key is None:
            return []
        skus = self._product_skus(key, product_id)
        if not skus:
            return []
        sku_clause = " OR ".join(f'sku:"{s}"' for s in skus)
        parts = [f"({sku_clause})"]
        if since:
            parts.append(f"processed_at:>={_iso_z(since)}")
        if until:
            parts.append(f"processed_at:<{_iso_z(until)}")
        query = " ".join(parts)

        out: list[Order] = []
        for node in iter_all_nodes(
            lambda after: self._query(
                key, _LIST_ORDERS, {"query": query, "first": 100, "after": after}
            ).get("orders")
        ):
            order = self._to_order(store_id, node)
            if any(li.product_id == product_id for li in order.line_items):
                out.append(order)
            if limit is not None and len(out) >= limit:
                break
        return out

    def sales_by_day_for_product(
        self, store_id: StoreId, product_id: ProductId, since: datetime, until: datetime
    ) -> tuple[ProductSalesDay, ...]:
        orders = self._orders_with_product(
            store_id, product_id, since=since, until=until, limit=None
        )
        by_day: dict[date, dict[str, Any]] = {}
        for order in orders:
            day = order.processed_at.astimezone(UTC).date()
            bucket = by_day.setdefault(day, {"units": 0, "gross": Decimal("0"), "orders": set()})
            matched = False
            for li in order.line_items:
                if li.product_id != product_id:
                    continue
                matched = True
                bucket["units"] += li.quantity
                bucket["gross"] += (li.price * li.quantity) - li.total_discount
            if matched:
                bucket["orders"].add(order.legacy_id)
        return tuple(
            ProductSalesDay(
                date=day,
                units=int(b["units"]),
                gross_revenue=b["gross"],
                order_count=len(b["orders"]),
            )
            for day, b in sorted(by_day.items())
            if b["orders"]
        )

    def find_orders_containing_product(
        self, store_id: StoreId, product_id: ProductId, *, limit: int = 20
    ) -> tuple[Order, ...]:
        orders = self._orders_with_product(
            store_id, product_id, since=None, until=None, limit=limit
        )
        orders.sort(key=lambda o: o.processed_at, reverse=True)
        return tuple(orders[:limit])

    # -- writes (no-op in live mode) --------------------------------------

    def upsert(self, order: Order) -> None:  # noqa: ARG002 — freshness comes from live reads
        return None
