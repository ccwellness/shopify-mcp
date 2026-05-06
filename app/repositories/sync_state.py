"""SqlAlchemySyncStateRepository — concrete `SyncStateRepository`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.orm.sync_state import SyncStateRowOrm
from app.domain.enums import SyncResource
from app.domain.models import StoreId, SyncStateRow


def _row_to_domain(row: SyncStateRowOrm) -> SyncStateRow:
    return SyncStateRow(
        store_id=StoreId(row.store_id),
        resource=SyncResource(row.resource),
        last_completed_at=row.last_completed_at,
        last_cursor=row.last_cursor,
        last_error=row.last_error,
        last_error_at=row.last_error_at,
        updated_at=row.updated_at,
    )


class SqlAlchemySyncStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, store_id: StoreId, resource: SyncResource) -> SyncStateRow | None:
        row = self._session.get(SyncStateRowOrm, (int(store_id), resource.value))
        return _row_to_domain(row) if row else None

    def list_for_store(self, store_id: StoreId) -> tuple[SyncStateRow, ...]:
        rows = self._session.scalars(
            select(SyncStateRowOrm)
            .where(SyncStateRowOrm.store_id == int(store_id))
            .order_by(SyncStateRowOrm.resource)
        ).all()
        return tuple(_row_to_domain(r) for r in rows)

    def upsert(self, row: SyncStateRow) -> None:
        stmt = pg_insert(SyncStateRowOrm).values(
            store_id=int(row.store_id),
            resource=row.resource.value,
            last_completed_at=row.last_completed_at,
            last_cursor=row.last_cursor,
            last_error=row.last_error,
            last_error_at=row.last_error_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["store_id", "resource"],
            set_={
                "last_completed_at": stmt.excluded.last_completed_at,
                "last_cursor": stmt.excluded.last_cursor,
                "last_error": stmt.excluded.last_error,
                "last_error_at": stmt.excluded.last_error_at,
            },
        )
        self._session.execute(stmt)
        self._session.flush()
