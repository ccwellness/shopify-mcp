"""Live InventoryRepository — reads inventory levels from Shopify GraphQL.

Inventory item counts are modest (far fewer than orders), so list paging is
implemented as a simple offset over the fully-materialized, deterministically
sorted level set — correct and easy to reason about, at the cost of re-fetching
on each page.
"""

from __future__ import annotations

import base64

from app.domain.models import (
    InventoryItem,
    InventoryLevel,
    Page,
    StoreId,
    VariantId,
)
from app.domain.specs import InventorySpec
from app.shopify.live_paging import iter_all_nodes
from app.shopify.normalizers.inventory_paginated import normalize_inventory_item_live
from app.shopify.repositories._base import _LiveRepo, variant_gid

_INV_ITEM = """
  id legacyResourceId sku tracked
  variant { legacyResourceId }
  inventoryLevels(first: 50) {
    edges { node {
      location { legacyResourceId }
      quantities(names: ["available", "on_hand", "committed", "incoming"]) {
        name quantity
      }
    } }
  }
"""

_LIST_VARIANTS = f"""
query LiveInvVariants($query: String, $first: Int!, $after: String) {{
  productVariants(first: $first, after: $after, query: $query) {{
    edges {{ node {{ inventoryItem {{ {_INV_ITEM} }} }} }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

_GET_VARIANT = f"""
query LiveInvVariant($id: ID!) {{
  productVariant(id: $id) {{ inventoryItem {{ {_INV_ITEM} }} }}
}}
"""


def _encode_offset(offset: int) -> str:
    return base64.urlsafe_b64encode(f"off|{offset}".encode()).decode("ascii").rstrip("=")


def _decode_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    pad = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(cursor + pad).decode()
    _, _, n = raw.partition("|")
    return int(n) if n.isdigit() else 0


class LiveInventoryRepository(_LiveRepo):
    def _levels_for_store(self, store_id: StoreId, sku: str | None) -> list[InventoryLevel]:
        key = self._key(store_id)
        if key is None:
            return []
        query = f'sku:"{sku.replace(chr(34), "")}"' if sku else None
        levels: list[InventoryLevel] = []
        for node in iter_all_nodes(
            lambda after: self._query(
                key, _LIST_VARIANTS, {"query": query, "first": 100, "after": after}
            ).get("productVariants")
        ):
            item_node = (node or {}).get("inventoryItem")
            if not item_node:
                continue
            normalized = normalize_inventory_item_live(store_id, item_node)
            levels.extend(normalized.levels)
        return levels

    def list_levels(
        self, spec: InventorySpec, *, limit: int = 50, cursor: str | None = None
    ) -> Page[InventoryLevel]:
        keys = self._index.resolve_keys(spec.store_ids)
        all_levels: list[InventoryLevel] = []
        for key in keys:
            store_id = self._index.id_for(key)
            if store_id is None:
                continue
            for level in self._levels_for_store(store_id, spec.sku):
                if spec.location_id is not None and level.location_id != spec.location_id:
                    continue
                if spec.low_stock_threshold is not None:
                    if level.available is None or level.available >= spec.low_stock_threshold:
                        continue
                all_levels.append(level)

        all_levels.sort(
            key=lambda lv: (int(lv.store_id), int(lv.location_id), int(lv.inventory_item_id))
        )
        offset = _decode_offset(cursor)
        window = all_levels[offset : offset + limit]
        next_cursor = _encode_offset(offset + limit) if offset + limit < len(all_levels) else None
        return Page(items=tuple(window), next_cursor=next_cursor)

    def levels_for_variants(
        self, store_id: StoreId, variant_ids: tuple[VariantId, ...]
    ) -> tuple[InventoryLevel, ...]:
        key = self._key(store_id)
        if key is None:
            return ()
        out: list[InventoryLevel] = []
        for vid in variant_ids:
            data = self._query(key, _GET_VARIANT, {"id": variant_gid(int(vid))})
            variant = data.get("productVariant") or {}
            item_node = variant.get("inventoryItem")
            if item_node:
                out.extend(normalize_inventory_item_live(store_id, item_node).levels)
        return tuple(out)

    def get_item(self, store_id: StoreId, gid: str) -> InventoryItem | None:
        key = self._key(store_id)
        if key is None:
            return None
        # Resolve via the owning variant is awkward; callers in the live MCP
        # path don't use this, so a direct inventoryItem fetch suffices.
        data = self._query(
            key,
            f"query LiveInvItem($id: ID!) {{ inventoryItem(id: $id) {{ {_INV_ITEM} }} }}",
            {"id": gid},
        )
        item_node = data.get("inventoryItem")
        return normalize_inventory_item_live(store_id, item_node).item if item_node else None

    def list_low_stock(
        self, store_id: StoreId, threshold: int, *, limit: int = 50
    ) -> tuple[InventoryLevel, ...]:
        spec = InventorySpec(store_ids=(store_id,), low_stock_threshold=threshold)
        return self.list_levels(spec, limit=limit).items

    def upsert_item(self, item: InventoryItem) -> None:  # noqa: ARG002
        return None

    def upsert_level(self, level: InventoryLevel) -> None:  # noqa: ARG002
        return None
