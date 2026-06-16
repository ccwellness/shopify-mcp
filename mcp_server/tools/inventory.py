"""`list_low_stock` + `check_inventory` MCP tools."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models import LocationId, StoreId
from mcp_server.audit import audited
from mcp_server.server import mcp, services

_MAX_LIMIT = 50
_DEFAULT_THRESHOLD = 10


class LevelOut(BaseModel):
    id: int
    store_id: int
    inventory_item_id: int
    location_id: int
    available: int | None
    on_hand: int | None
    committed: int | None
    incoming: int | None
    updated_at: datetime | None


class LowStockPageOut(BaseModel):
    items: list[LevelOut]
    next_cursor: str | None
    threshold: int


@mcp.tool
@audited("list_low_stock")
def list_low_stock(  # noqa: PLR0913 — flat filter args mirror REST + GraphQL
    store_id: list[int] | None = Field(  # noqa: B008 — Pydantic Field-as-default is the idiom
        default=None, description="Optional list of numeric store ids."
    ),
    threshold: int = Field(
        default=_DEFAULT_THRESHOLD,
        ge=0,
        description="Levels with `available < threshold` are returned.",
    ),
    location_id: int | None = Field(default=None, description="Restrict to one location."),
    sku: str | None = Field(default=None, description="Restrict to one SKU."),
    limit: int = Field(default=50, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Field(default=None),
) -> LowStockPageOut:
    """Inventory levels below `threshold`.

    Levels with `available IS NULL` are excluded — we can't say something
    is low if we don't know how much we have. Use list_stores first if
    you need a specific store_id.
    """
    page = services().inventory.list_low_stock(
        store_ids=tuple(StoreId(s) for s in store_id) if store_id else None,
        threshold=threshold,
        location_id=LocationId(location_id) if location_id is not None else None,
        sku=sku or None,
        limit=limit,
        cursor=cursor,
    )
    return LowStockPageOut(
        items=[
            LevelOut(
                id=int(lvl.id),
                store_id=int(lvl.store_id),
                inventory_item_id=int(lvl.inventory_item_id),
                location_id=int(lvl.location_id),
                available=lvl.available,
                on_hand=lvl.on_hand,
                committed=lvl.committed,
                incoming=lvl.incoming,
                updated_at=lvl.updated_at,
            )
            for lvl in page.items
        ],
        next_cursor=page.next_cursor,
        threshold=threshold,
    )


class InventoryPageOut(BaseModel):
    items: list[LevelOut]
    next_cursor: str | None


@mcp.tool
@audited("check_inventory")
def check_inventory(
    sku: str | None = Field(
        default=None, description="Exact SKU to look up. Either this or location_id must be set."
    ),
    store_id: list[int] | None = Field(  # noqa: B008
        default=None, description="Optional list of numeric store ids."
    ),
    location_id: int | None = Field(
        default=None, description="Restrict to one location (e.g. a specific warehouse)."
    ),
    limit: int = Field(default=50, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Field(default=None),
) -> InventoryPageOut:
    """Current inventory levels for a SKU (or every level at a location).

    Companion to `list_low_stock` — this one returns every matching level
    regardless of how high `available` is, so an agent can answer
    "how many do we have on hand?" without an implicit threshold gate.
    Either `sku` or `location_id` must be provided to keep the result
    set bounded.
    """
    if not sku and location_id is None:
        raise ValueError("either sku or location_id is required")

    page = services().inventory.list_levels(
        store_ids=tuple(StoreId(s) for s in store_id) if store_id else None,
        location_id=LocationId(location_id) if location_id is not None else None,
        sku=sku or None,
        limit=limit,
        cursor=cursor,
    )
    return InventoryPageOut(
        items=[
            LevelOut(
                id=int(lvl.id),
                store_id=int(lvl.store_id),
                inventory_item_id=int(lvl.inventory_item_id),
                location_id=int(lvl.location_id),
                available=lvl.available,
                on_hand=lvl.on_hand,
                committed=lvl.committed,
                incoming=lvl.incoming,
                updated_at=lvl.updated_at,
            )
            for lvl in page.items
        ],
        next_cursor=page.next_cursor,
    )
