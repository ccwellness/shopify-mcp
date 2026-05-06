"""Shopify bulk-operations Product JSON → domain Product (with Variants).

The bulk products query emits Product nodes followed by their nested
ProductVariant children. The JSONL grouper attaches variants under the
parent's `variants` field via the `__parentId` linkage.

GraphQL specifics:

  - `status` is a `ProductStatus` enum: ACTIVE / ARCHIVED / DRAFT.
    Lowercases cleanly onto our `ProductStatus` enum.
  - `tags` arrives as a list of strings already.
  - Variant `price` / `compareAtPrice` arrive as plain decimal strings
    in the shop's currency (no MoneyV2 wrapper on these scalar fields).
  - Variant `inventoryItem.id` is the GID we'll later use to link
    InventoryItemRow rows; we surface it in the normalized domain object
    via `inventory_item_id` so the inventory normalizer can resolve it.
    The dataclass field stores it as `InventoryItemId | None` though —
    the variant normalizer can't know the DB id yet, so it stays None
    here. The GID linkage is on the bulk inventory side.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.enums import ProductStatus
from app.domain.models import (
    Product,
    ProductId,
    StoreId,
    Variant,
    VariantId,
)


def _money(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def _opt_money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _required_ts(value: Any, field: str) -> datetime:
    if value is None or value == "":
        raise ValueError(f"required datetime field {field!r} missing or empty")
    return datetime.fromisoformat(value)


def _legacy_id(value: Any) -> int:
    if value is None or value == "":
        raise ValueError("legacyResourceId missing")
    return int(str(value))


def _opt_legacy_id(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value))


def _product_status(value: str | None) -> ProductStatus:
    if not value:
        return ProductStatus.ACTIVE
    try:
        return ProductStatus(value.lower())
    except ValueError:
        return ProductStatus.ACTIVE


def _normalize_variant(store_id: StoreId, payload: dict[str, Any]) -> Variant:
    return Variant(
        id=VariantId(0),
        store_id=store_id,
        product_id=ProductId(0),  # filled by repo via parent relationship
        gid=str(payload["id"]),
        legacy_id=_legacy_id(payload.get("legacyResourceId")),
        title=str(payload.get("title") or ""),
        sku=payload.get("sku"),
        barcode=payload.get("barcode"),
        position=_opt_legacy_id(payload.get("position")),
        price=_money(payload.get("price")),
        compare_at_price=_opt_money(payload.get("compareAtPrice")),
        # Bulk variants don't expose currencyCode at the variant level —
        # it's the shop default. Left as None; analytics joins on Order.
        currency_code=None,
        inventory_item_id=None,
    )


def normalize_product_bulk(store_id: StoreId, payload: dict[str, Any]) -> Product:
    variants = tuple(
        _normalize_variant(store_id, v)
        for v in (payload.get("variants") or [])
        if isinstance(v, dict)
    )
    raw_tags = payload.get("tags") or []
    tags = tuple(str(t) for t in raw_tags if t)

    return Product(
        id=ProductId(0),
        store_id=store_id,
        gid=str(payload["id"]),
        legacy_id=_legacy_id(payload.get("legacyResourceId")),
        title=str(payload.get("title") or ""),
        handle=str(payload.get("handle") or ""),
        status=_product_status(payload.get("status")),
        vendor=payload.get("vendor"),
        product_type=payload.get("productType"),
        tags=tags,
        created_at=_required_ts(payload.get("createdAt"), "product.createdAt"),
        updated_at=_required_ts(payload.get("updatedAt"), "product.updatedAt"),
        variants=variants,
    )
