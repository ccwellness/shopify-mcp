"""Live ProductRepository — reads the catalog from Shopify GraphQL."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

from app.domain.models import (
    InventoryItemId,
    Page,
    Product,
    ProductId,
    StoreId,
    VariantId,
)
from app.domain.specs import ProductSpec
from app.shopify.live_paging import flatten_edges, gid_tail
from app.shopify.normalizers.products_bulk import normalize_product_bulk
from app.shopify.repositories import _keyset
from app.shopify.repositories._base import _LiveRepo, product_gid

_PRODUCT_NODE = """
  id legacyResourceId title handle status vendor productType tags
  createdAt updatedAt
  variants(first: 100) {
    edges { node {
      id legacyResourceId title sku barcode position price compareAtPrice
      inventoryItem { id legacyResourceId }
    } }
  }
"""

_GET_PRODUCT = f"query LiveProduct($id: ID!) {{ product(id: $id) {{ {_PRODUCT_NODE} }} }}"

_LIST_PRODUCTS = f"""
query LiveProducts($query: String, $first: Int!, $after: String) {{
  products(first: $first, after: $after, query: $query,
           sortKey: UPDATED_AT, reverse: true) {{
    edges {{ cursor node {{ {_PRODUCT_NODE} }} }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class LiveProductRepository(_LiveRepo):
    def _to_product(self, store_id: StoreId, node: dict[str, Any]) -> Product:
        payload = dict(node)
        edges = (payload.get("variants") or {}).get("edges") or []
        raw_variants = [e["node"] for e in edges if isinstance(e, dict) and e.get("node")]
        payload["variants"] = raw_variants

        normalized = normalize_product_bulk(store_id, payload)
        new_pid = ProductId(normalized.legacy_id)
        variants = []
        for raw, v in zip(raw_variants, normalized.variants, strict=False):
            inv = raw.get("inventoryItem") or {}
            inv_legacy = gid_tail(inv.get("id"))
            variants.append(
                dataclasses.replace(
                    v,
                    id=VariantId(v.legacy_id),
                    product_id=new_pid,
                    inventory_item_id=(
                        InventoryItemId(inv_legacy) if inv_legacy is not None else None
                    ),
                )
            )
        return dataclasses.replace(normalized, id=new_pid, variants=tuple(variants))

    def get(self, product_id: ProductId) -> Product | None:
        for store_id in self._index.all_store_ids():
            found = self.get_by_gid(store_id, product_gid(int(product_id)))
            if found is not None:
                return found
        return None

    def get_by_gid(self, store_id: StoreId, gid: str) -> Product | None:
        key = self._key(store_id)
        if key is None:
            return None
        data = self._query(key, _GET_PRODUCT, {"id": gid})
        node = data.get("product")
        return self._to_product(store_id, node) if node else None

    def get_by_handle(self, store_id: StoreId, handle: str) -> Product | None:
        spec = ProductSpec(store_ids=(store_id,), handle=handle)
        page = self.find(spec, limit=1)
        return page.items[0] if page.items else None

    def _build_query(self, spec: ProductSpec, *, watermark_ts: datetime | None) -> str | None:
        parts: list[str] = []
        if spec.status is not None:
            parts.append(f"status:{spec.status.value}")
        if spec.title_query:
            parts.append(f"title:*{spec.title_query.replace(chr(34), '')}*")
        if spec.handle:
            parts.append(f'handle:"{spec.handle.replace(chr(34), "")}"')
        if spec.vendor:
            parts.append(f'vendor:"{spec.vendor.replace(chr(34), "")}"')
        if spec.product_type:
            parts.append(f'product_type:"{spec.product_type.replace(chr(34), "")}"')
        if spec.tag:
            parts.append(f'tag:"{spec.tag.replace(chr(34), "")}"')
        if watermark_ts is not None:
            parts.append(f"updated_at:<={_iso_z(watermark_ts)}")
        return " ".join(parts) if parts else None

    def find(
        self, spec: ProductSpec, *, limit: int = 50, cursor: str | None = None
    ) -> Page[Product]:
        keys = self._index.resolve_keys(spec.store_ids)
        if not keys:
            return Page(items=(), next_cursor=None)

        watermark = _keyset.decode(cursor) if cursor else None
        query = self._build_query(spec, watermark_ts=watermark[0] if watermark else None)

        candidates: list[Product] = []
        any_more = False
        for key in keys:
            store_id = self._index.id_for(key)
            if store_id is None:
                continue
            data = self._query(key, _LIST_PRODUCTS, {"query": query, "first": limit, "after": None})
            nodes, next_cursor = flatten_edges(data.get("products"))
            any_more = any_more or next_cursor is not None
            for node in nodes:
                product = self._to_product(store_id, node)
                if watermark is not None:
                    ts, pid = watermark
                    if product.updated_at > ts or (
                        product.updated_at == ts and product.legacy_id >= pid
                    ):
                        continue
                candidates.append(product)

        candidates.sort(key=lambda p: (p.updated_at, p.legacy_id), reverse=True)
        page = candidates[:limit]
        has_more = len(candidates) > limit or (any_more and len(page) == limit)
        next_cur = (
            _keyset.encode(page[-1].updated_at, page[-1].legacy_id) if page and has_more else None
        )
        return Page(items=tuple(page), next_cursor=next_cur)

    # -- sync-only methods: unsupported in live mode ----------------------

    def variant_gid_map(self, store_id: StoreId) -> dict[str, VariantId]:
        raise NotImplementedError("variant_gid_map is a sync-only method")

    def product_gid_map(self, store_id: StoreId) -> dict[str, ProductId]:
        raise NotImplementedError("product_gid_map is a sync-only method")

    def upsert(self, product: Product) -> None:  # noqa: ARG002
        return None
