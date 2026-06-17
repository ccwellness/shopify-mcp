"""Shopify paginated inventory query → domain InventoryItem + InventoryLevels.

Inventory uses a regular paginated GraphQL query rather than bulk operations
because Shopify's bulk runner does not support list-of-objects fields like
`InventoryLevel.quantities(names: [...])` nested inside a connection child.
The trade-off is fine: typical merchants have far fewer inventory items
than orders, so paginated GraphQL completes in seconds.

The query shape (defined in services/sync.py) returns InventoryItem nodes
with an inline `inventoryLevels` connection. Each level carries its location
GID and a `quantities` list naming the four state buckets we track:
`available`, `on_hand`, `committed`, `incoming`.

This normalizer is paired with two GID→DB-id lookup tables resolved upstream
in SyncService: variants_by_gid and locations_by_gid. Levels whose location
GID is not yet known to us (e.g. a brand-new location not yet synced) are
skipped — the next reconcile cycle picks them up after sync_locations runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.models import (
    InventoryItem,
    InventoryItemId,
    InventoryLevel,
    InventoryLevelId,
    LocationId,
    StoreId,
    VariantId,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedInventory:
    """One inventory item plus all of its location-keyed levels.

    `levels` is keyed by location_id (already resolved by the caller) so
    SyncService can upsert each in a single pass without re-querying.
    """

    item: InventoryItem
    levels: tuple[InventoryLevel, ...]


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _quantities_to_dict(quantities: list[dict[str, Any]] | None) -> dict[str, int | None]:
    """Flatten `[{name: 'available', quantity: 5}, ...]` into a name→qty map."""
    out: dict[str, int | None] = {}
    if not quantities:
        return out
    for q in quantities:
        if not isinstance(q, dict):
            continue
        name = q.get("name")
        if not name:
            continue
        out[str(name)] = _opt_int(q.get("quantity"))
    return out


def _legacy_id(value: Any) -> int:
    if value is None or value == "":
        raise ValueError("legacyResourceId missing")
    return int(str(value))


def normalize_inventory_item(
    store_id: StoreId,
    payload: dict[str, Any],
    *,
    variants_by_gid: dict[str, VariantId],
    locations_by_gid: dict[str, LocationId],
) -> NormalizedInventory:
    """Build domain InventoryItem + InventoryLevel children.

    Levels missing a known location_id are dropped. `variant_id` is None
    when the item's parent variant hasn't been synced into our DB yet.
    """
    variant = payload.get("variant") or {}
    variant_gid = variant.get("id")
    variant_id: VariantId | None = variants_by_gid.get(str(variant_gid)) if variant_gid else None

    item = InventoryItem(
        id=InventoryItemId(0),
        store_id=store_id,
        variant_id=variant_id,
        gid=str(payload["id"]),
        legacy_id=_legacy_id(payload.get("legacyResourceId")),
        sku=payload.get("sku"),
        tracked=bool(payload.get("tracked", True)),
    )

    levels: list[InventoryLevel] = []
    inventory_levels = payload.get("inventoryLevels") or {}
    edges = inventory_levels.get("edges") or []
    now = datetime.now(tz=UTC)
    for edge in edges:
        node = (edge or {}).get("node") or {}
        location = node.get("location") or {}
        loc_gid = location.get("id")
        if not loc_gid:
            continue
        location_id = locations_by_gid.get(str(loc_gid))
        if location_id is None:
            # Level on a location we haven't synced yet — skip; next pass picks it up.
            continue
        qty_map = _quantities_to_dict(node.get("quantities"))
        levels.append(
            InventoryLevel(
                id=InventoryLevelId(0),
                store_id=store_id,
                # filled by repo after upsert_item; kept here as a placeholder
                inventory_item_id=InventoryItemId(0),
                location_id=location_id,
                available=qty_map.get("available"),
                on_hand=qty_map.get("on_hand"),
                committed=qty_map.get("committed"),
                incoming=qty_map.get("incoming"),
                updated_at=now,
            )
        )

    return NormalizedInventory(item=item, levels=tuple(levels))


def normalize_inventory_item_live(
    store_id: StoreId,
    payload: dict[str, Any],
) -> NormalizedInventory:
    """DB-free variant: derive all ids from Shopify `legacyResourceId`.

    Unlike `normalize_inventory_item`, this keeps every level (no location is
    "unknown" in live mode) and resolves variant/location/item ids from the
    GraphQL `legacyResourceId` fields, so the query must select them.
    """
    variant = payload.get("variant") or {}
    variant_legacy = _opt_legacy_id(variant.get("legacyResourceId"))
    item_legacy = _legacy_id(payload.get("legacyResourceId"))

    item = InventoryItem(
        id=InventoryItemId(item_legacy),
        store_id=store_id,
        variant_id=VariantId(variant_legacy) if variant_legacy is not None else None,
        gid=str(payload["id"]),
        legacy_id=item_legacy,
        sku=payload.get("sku"),
        tracked=bool(payload.get("tracked", True)),
    )

    levels: list[InventoryLevel] = []
    inventory_levels = payload.get("inventoryLevels") or {}
    edges = inventory_levels.get("edges") or []
    now = datetime.now(tz=UTC)
    for edge in edges:
        node = (edge or {}).get("node") or {}
        location = node.get("location") or {}
        location_legacy = _opt_legacy_id(location.get("legacyResourceId"))
        if location_legacy is None:
            continue
        qty_map = _quantities_to_dict(node.get("quantities"))
        levels.append(
            InventoryLevel(
                id=InventoryLevelId(0),
                store_id=store_id,
                inventory_item_id=InventoryItemId(item_legacy),
                location_id=LocationId(location_legacy),
                available=qty_map.get("available"),
                on_hand=qty_map.get("on_hand"),
                committed=qty_map.get("committed"),
                incoming=qty_map.get("incoming"),
                updated_at=now,
            )
        )

    return NormalizedInventory(item=item, levels=tuple(levels))


def _opt_legacy_id(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
