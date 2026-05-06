"""SqlAlchemyProductRepository — concrete `ProductRepository`.

Product is an aggregate (Product + Variant). On upsert, variants are
fully replaced — simple and correct for v1's reconcile-on-write pattern.
"""

from __future__ import annotations

from sqlalchemy import literal, select, tuple_
from sqlalchemy.orm import Session

from app.db.orm.product import ProductRow, VariantRow
from app.domain.enums import ProductStatus
from app.domain.models import (
    InventoryItemId,
    Page,
    Product,
    ProductId,
    StoreId,
    Variant,
    VariantId,
)
from app.domain.specs import ProductSpec
from app.repositories._cursor import decode, encode


def _variant_row_to_domain(row: VariantRow) -> Variant:
    return Variant(
        id=VariantId(row.id),
        store_id=StoreId(row.store_id),
        product_id=ProductId(row.product_id),
        gid=row.gid,
        legacy_id=row.legacy_id,
        title=row.title,
        sku=row.sku,
        barcode=row.barcode,
        position=row.position,
        price=row.price,
        compare_at_price=row.compare_at_price,
        currency_code=row.currency_code,
        inventory_item_id=None,
    )


def _row_to_domain(row: ProductRow) -> Product:
    return Product(
        id=ProductId(row.id),
        store_id=StoreId(row.store_id),
        gid=row.gid,
        legacy_id=row.legacy_id,
        title=row.title,
        handle=row.handle,
        status=ProductStatus(row.status),
        vendor=row.vendor,
        product_type=row.product_type,
        tags=tuple(row.tags or ()),
        created_at=row.created_at,
        updated_at=row.updated_at,
        variants=tuple(_variant_row_to_domain(v) for v in row.variants),
    )


def _variant_to_row(variant: Variant) -> VariantRow:
    return VariantRow(
        store_id=int(variant.store_id),
        gid=variant.gid,
        legacy_id=variant.legacy_id,
        title=variant.title,
        sku=variant.sku,
        barcode=variant.barcode,
        position=variant.position,
        price=variant.price,
        compare_at_price=variant.compare_at_price,
        currency_code=variant.currency_code,
    )


class SqlAlchemyProductRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, product_id: ProductId) -> Product | None:
        row = self._session.get(ProductRow, int(product_id))
        return _row_to_domain(row) if row else None

    def get_by_gid(self, store_id: StoreId, gid: str) -> Product | None:
        row = self._session.scalar(
            select(ProductRow).where(
                ProductRow.store_id == int(store_id),
                ProductRow.gid == gid,
            )
        )
        return _row_to_domain(row) if row else None

    def get_by_handle(self, store_id: StoreId, handle: str) -> Product | None:
        row = self._session.scalar(
            select(ProductRow).where(
                ProductRow.store_id == int(store_id),
                ProductRow.handle == handle,
            )
        )
        return _row_to_domain(row) if row else None

    def find(
        self,
        spec: ProductSpec,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[Product]:
        stmt = select(ProductRow)
        if spec.store_ids is not None:
            stmt = stmt.where(ProductRow.store_id.in_([int(s) for s in spec.store_ids]))
        if spec.status is not None:
            stmt = stmt.where(ProductRow.status == spec.status.value)
        if spec.title_query:
            stmt = stmt.where(ProductRow.title.ilike(f"%{spec.title_query}%"))
        if spec.handle:
            stmt = stmt.where(ProductRow.handle == spec.handle)
        if spec.vendor:
            stmt = stmt.where(ProductRow.vendor == spec.vendor)
        if spec.product_type:
            stmt = stmt.where(ProductRow.product_type == spec.product_type)
        if spec.tag:
            stmt = stmt.where(ProductRow.tags.contains([spec.tag]))
        if cursor:
            cur_updated_at, cur_id = decode(cursor)
            stmt = stmt.where(
                tuple_(ProductRow.updated_at, ProductRow.id)
                < tuple_(literal(cur_updated_at), literal(cur_id))
            )
        stmt = stmt.order_by(ProductRow.updated_at.desc(), ProductRow.id.desc()).limit(limit + 1)
        rows = self._session.scalars(stmt).all()
        items = [_row_to_domain(r) for r in rows[:limit]]
        next_cursor = (
            encode(rows[limit - 1].updated_at, rows[limit - 1].id) if len(rows) > limit else None
        )
        return Page(items=tuple(items), next_cursor=next_cursor)

    def variant_gid_map(self, store_id: StoreId) -> dict[str, VariantId]:
        rows = self._session.execute(
            select(VariantRow.gid, VariantRow.id).where(VariantRow.store_id == int(store_id))
        ).all()
        return {gid: VariantId(vid) for gid, vid in rows}

    def product_gid_map(self, store_id: StoreId) -> dict[str, ProductId]:
        rows = self._session.execute(
            select(ProductRow.gid, ProductRow.id).where(ProductRow.store_id == int(store_id))
        ).all()
        return {gid: ProductId(pid) for gid, pid in rows}

    def upsert(self, product: Product) -> None:
        existing = self._session.scalar(
            select(ProductRow).where(
                ProductRow.store_id == int(product.store_id),
                ProductRow.gid == product.gid,
            )
        )
        if existing is None:
            row = ProductRow(
                store_id=int(product.store_id),
                gid=product.gid,
                legacy_id=product.legacy_id,
                title=product.title,
                handle=product.handle,
                status=product.status.value,
                vendor=product.vendor,
                product_type=product.product_type,
                tags=list(product.tags),
                variants=[_variant_to_row(v) for v in product.variants],
            )
            self._session.add(row)
        else:
            existing.legacy_id = product.legacy_id
            existing.title = product.title
            existing.handle = product.handle
            existing.status = product.status.value
            existing.vendor = product.vendor
            existing.product_type = product.product_type
            existing.tags = list(product.tags)
            # Replace variants wholesale; .clear() cascades delete-orphan.
            existing.variants.clear()
            self._session.flush()
            for v in product.variants:
                existing.variants.append(_variant_to_row(v))
        self._session.flush()


# Re-export to silence "imported but unused" — InventoryItemId is reserved for a
# future iteration that wires variants ↔ inventory_items.
_ = InventoryItemId
