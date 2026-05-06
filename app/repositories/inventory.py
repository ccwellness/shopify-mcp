"""SqlAlchemyInventoryRepository — concrete `InventoryRepository`."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import literal, select, tuple_
from sqlalchemy.orm import Session

from app.db.orm.inventory import InventoryItemRow, InventoryLevelRow
from app.domain.models import (
    InventoryItem,
    InventoryItemId,
    InventoryLevel,
    InventoryLevelId,
    LocationId,
    Page,
    StoreId,
    VariantId,
)
from app.domain.specs import InventorySpec
from app.repositories._cursor import decode, encode


def _item_row_to_domain(row: InventoryItemRow) -> InventoryItem:
    return InventoryItem(
        id=InventoryItemId(row.id),
        store_id=StoreId(row.store_id),
        variant_id=VariantId(row.variant_id) if row.variant_id is not None else None,
        gid=row.gid,
        legacy_id=row.legacy_id,
        sku=row.sku,
        tracked=row.tracked,
    )


def _level_row_to_domain(row: InventoryLevelRow) -> InventoryLevel:
    return InventoryLevel(
        id=InventoryLevelId(row.id),
        store_id=StoreId(row.store_id),
        inventory_item_id=InventoryItemId(row.inventory_item_id),
        location_id=LocationId(row.location_id),
        available=row.available,
        on_hand=row.on_hand,
        committed=row.committed,
        incoming=row.incoming,
        updated_at=row.updated_at,
    )


class SqlAlchemyInventoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_levels(
        self,
        spec: InventorySpec,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[InventoryLevel]:
        stmt = select(InventoryLevelRow)
        if spec.store_ids is not None:
            stmt = stmt.where(InventoryLevelRow.store_id.in_([int(s) for s in spec.store_ids]))
        if spec.location_id is not None:
            stmt = stmt.where(InventoryLevelRow.location_id == int(spec.location_id))
        if spec.sku:
            stmt = stmt.join(
                InventoryItemRow, InventoryItemRow.id == InventoryLevelRow.inventory_item_id
            ).where(InventoryItemRow.sku == spec.sku)
        if spec.low_stock_threshold is not None:
            stmt = stmt.where(InventoryLevelRow.available < spec.low_stock_threshold)
        if cursor:
            cur_updated_at, cur_id = decode(cursor)
            stmt = stmt.where(
                tuple_(InventoryLevelRow.updated_at, InventoryLevelRow.id)
                < tuple_(literal(cur_updated_at), literal(cur_id))
            )
        stmt = stmt.order_by(
            InventoryLevelRow.updated_at.desc(), InventoryLevelRow.id.desc()
        ).limit(limit + 1)
        rows = self._session.scalars(stmt).all()
        items = [_level_row_to_domain(r) for r in rows[:limit]]
        next_cursor = (
            encode(rows[limit - 1].updated_at, rows[limit - 1].id) if len(rows) > limit else None
        )
        return Page(items=tuple(items), next_cursor=next_cursor)

    def get_item(self, store_id: StoreId, gid: str) -> InventoryItem | None:
        row = self._session.scalar(
            select(InventoryItemRow).where(
                InventoryItemRow.store_id == int(store_id),
                InventoryItemRow.gid == gid,
            )
        )
        return _item_row_to_domain(row) if row else None

    def list_low_stock(
        self,
        store_id: StoreId,
        threshold: int,
        *,
        limit: int = 50,
    ) -> tuple[InventoryLevel, ...]:
        rows = self._session.scalars(
            select(InventoryLevelRow)
            .where(
                InventoryLevelRow.store_id == int(store_id),
                InventoryLevelRow.available < threshold,
            )
            .order_by(InventoryLevelRow.available.asc())
            .limit(limit)
        ).all()
        return tuple(_level_row_to_domain(r) for r in rows)

    def upsert_item(self, item: InventoryItem) -> None:
        existing = self._session.scalar(
            select(InventoryItemRow).where(
                InventoryItemRow.store_id == int(item.store_id),
                InventoryItemRow.gid == item.gid,
            )
        )
        if existing is None:
            self._session.add(
                InventoryItemRow(
                    store_id=int(item.store_id),
                    variant_id=int(item.variant_id) if item.variant_id is not None else None,
                    gid=item.gid,
                    legacy_id=item.legacy_id,
                    sku=item.sku,
                    tracked=item.tracked,
                )
            )
        else:
            existing.variant_id = int(item.variant_id) if item.variant_id is not None else None
            existing.legacy_id = item.legacy_id
            existing.sku = item.sku
            existing.tracked = item.tracked
            existing.last_seen_at = datetime.now(tz=UTC)
        self._session.flush()

    def upsert_level(self, level: InventoryLevel) -> None:
        existing = self._session.scalar(
            select(InventoryLevelRow).where(
                InventoryLevelRow.store_id == int(level.store_id),
                InventoryLevelRow.inventory_item_id == int(level.inventory_item_id),
                InventoryLevelRow.location_id == int(level.location_id),
            )
        )
        if existing is None:
            self._session.add(
                InventoryLevelRow(
                    store_id=int(level.store_id),
                    inventory_item_id=int(level.inventory_item_id),
                    location_id=int(level.location_id),
                    available=level.available,
                    on_hand=level.on_hand,
                    committed=level.committed,
                    incoming=level.incoming,
                )
            )
        else:
            existing.available = level.available
            existing.on_hand = level.on_hand
            existing.committed = level.committed
            existing.incoming = level.incoming
            existing.last_seen_at = datetime.now(tz=UTC)
        self._session.flush()
