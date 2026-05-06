"""SqlAlchemyLocationRepository — concrete `LocationRepository`."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.orm.location import LocationRow
from app.domain.models import Location, LocationId, StoreId


def _row_to_domain(row: LocationRow) -> Location:
    return Location(
        id=LocationId(row.id),
        store_id=StoreId(row.store_id),
        gid=row.gid,
        legacy_id=row.legacy_id,
        name=row.name,
        address1=row.address1,
        address2=row.address2,
        city=row.city,
        province=row.province,
        postal_code=row.postal_code,
        country=row.country,
        is_active=row.is_active,
        fulfills_online_orders=row.fulfills_online_orders,
        ships_inventory=row.ships_inventory,
        last_seen_at=row.last_seen_at,
    )


class SqlAlchemyLocationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_store(self, store_id: StoreId) -> tuple[Location, ...]:
        rows = self._session.scalars(
            select(LocationRow)
            .where(LocationRow.store_id == int(store_id))
            .order_by(LocationRow.name)
        ).all()
        return tuple(_row_to_domain(r) for r in rows)

    def get(self, location_id: LocationId) -> Location | None:
        row = self._session.get(LocationRow, int(location_id))
        return _row_to_domain(row) if row else None

    def get_by_gid(self, store_id: StoreId, gid: str) -> Location | None:
        row = self._session.scalar(
            select(LocationRow).where(
                LocationRow.store_id == int(store_id),
                LocationRow.gid == gid,
            )
        )
        return _row_to_domain(row) if row else None

    def upsert(self, location: Location) -> None:
        existing = self._session.scalar(
            select(LocationRow).where(
                LocationRow.store_id == int(location.store_id),
                LocationRow.gid == location.gid,
            )
        )
        if existing is None:
            self._session.add(
                LocationRow(
                    store_id=int(location.store_id),
                    gid=location.gid,
                    legacy_id=location.legacy_id,
                    name=location.name,
                    address1=location.address1,
                    address2=location.address2,
                    city=location.city,
                    province=location.province,
                    postal_code=location.postal_code,
                    country=location.country,
                    is_active=location.is_active,
                    fulfills_online_orders=location.fulfills_online_orders,
                    ships_inventory=location.ships_inventory,
                    last_seen_at=location.last_seen_at,
                )
            )
        else:
            existing.legacy_id = location.legacy_id
            existing.name = location.name
            existing.address1 = location.address1
            existing.address2 = location.address2
            existing.city = location.city
            existing.province = location.province
            existing.postal_code = location.postal_code
            existing.country = location.country
            existing.is_active = location.is_active
            existing.fulfills_online_orders = location.fulfills_online_orders
            existing.ships_inventory = location.ships_inventory
            existing.last_seen_at = datetime.now(tz=UTC)
        self._session.flush()
