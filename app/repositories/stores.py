"""SqlAlchemyStoreRepository — concrete `StoreRepository`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.orm.store import StoreRow
from app.domain.enums import SubscriptionProvider
from app.domain.models import Store, StoreId


def _row_to_domain(row: StoreRow) -> Store:
    return Store(
        id=StoreId(row.id),
        store_key=row.store_key,
        shop_domain=row.shop_domain,
        display_name=row.display_name,
        plus=row.plus,
        subscription_provider=SubscriptionProvider(row.subscription_provider),
        read_only=row.read_only,
        active=row.active,
        timezone=row.timezone,
        currency_code=row.currency_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyStoreRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_active(self) -> tuple[Store, ...]:
        rows = self._session.scalars(
            select(StoreRow).where(StoreRow.active.is_(True)).order_by(StoreRow.store_key)
        ).all()
        return tuple(_row_to_domain(r) for r in rows)

    def get(self, store_id: StoreId) -> Store | None:
        row = self._session.get(StoreRow, int(store_id))
        return _row_to_domain(row) if row else None

    def get_by_key(self, store_key: str) -> Store | None:
        row = self._session.scalar(select(StoreRow).where(StoreRow.store_key == store_key))
        return _row_to_domain(row) if row else None

    def get_by_domain(self, shop_domain: str) -> Store | None:
        row = self._session.scalar(select(StoreRow).where(StoreRow.shop_domain == shop_domain))
        return _row_to_domain(row) if row else None

    def upsert(self, store: Store) -> None:
        existing = self._session.scalar(
            select(StoreRow).where(StoreRow.store_key == store.store_key)
        )
        if existing is None:
            self._session.add(
                StoreRow(
                    store_key=store.store_key,
                    shop_domain=store.shop_domain,
                    display_name=store.display_name,
                    plus=store.plus,
                    subscription_provider=store.subscription_provider.value,
                    read_only=store.read_only,
                    active=store.active,
                    timezone=store.timezone,
                    currency_code=store.currency_code,
                )
            )
        else:
            existing.shop_domain = store.shop_domain
            existing.display_name = store.display_name
            existing.plus = store.plus
            existing.subscription_provider = store.subscription_provider.value
            existing.read_only = store.read_only
            existing.active = store.active
            existing.timezone = store.timezone
            existing.currency_code = store.currency_code
        self._session.flush()
