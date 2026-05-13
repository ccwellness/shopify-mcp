"""SqlAlchemyCustomerRepository — concrete `CustomerRepository`."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.orm.customer import CustomerRow
from app.domain.models import Customer, CustomerId, StoreId


def _row_to_domain(row: CustomerRow) -> Customer:
    return Customer(
        id=CustomerId(row.id),
        store_id=StoreId(row.store_id),
        gid=row.gid,
        legacy_id=row.legacy_id,
        email=row.email,
        phone=row.phone,
        first_name=row.first_name,
        last_name=row.last_name,
        accepts_marketing=row.accepts_marketing,
        orders_count=row.orders_count,
        total_spent=row.total_spent,
        currency_code=row.currency_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyCustomerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, customer_id: CustomerId) -> Customer | None:
        row = self._session.get(CustomerRow, int(customer_id))
        return _row_to_domain(row) if row else None

    def get_by_gid(self, store_id: StoreId, gid: str) -> Customer | None:
        row = self._session.scalar(
            select(CustomerRow).where(
                CustomerRow.store_id == int(store_id),
                CustomerRow.gid == gid,
            )
        )
        return _row_to_domain(row) if row else None

    def get_by_email(self, store_id: StoreId, email: str) -> Customer | None:
        row = self._session.scalar(
            select(CustomerRow).where(
                CustomerRow.store_id == int(store_id),
                CustomerRow.email == email,
            )
        )
        return _row_to_domain(row) if row else None

    def legacy_id_map(self, store_id: StoreId) -> dict[int, CustomerId]:
        rows = self._session.execute(
            select(CustomerRow.legacy_id, CustomerRow.id).where(
                CustomerRow.store_id == int(store_id)
            )
        ).all()
        return {legacy: CustomerId(cid) for legacy, cid in rows}

    def upsert(self, customer: Customer) -> None:
        existing = self._session.scalar(
            select(CustomerRow).where(
                CustomerRow.store_id == int(customer.store_id),
                CustomerRow.gid == customer.gid,
            )
        )
        if existing is None:
            self._session.add(
                CustomerRow(
                    store_id=int(customer.store_id),
                    gid=customer.gid,
                    legacy_id=customer.legacy_id,
                    email=customer.email,
                    phone=customer.phone,
                    first_name=customer.first_name,
                    last_name=customer.last_name,
                    accepts_marketing=customer.accepts_marketing,
                    orders_count=customer.orders_count,
                    total_spent=customer.total_spent,
                    currency_code=customer.currency_code,
                )
            )
        else:
            existing.legacy_id = customer.legacy_id
            existing.email = customer.email
            existing.phone = customer.phone
            existing.first_name = customer.first_name
            existing.last_name = customer.last_name
            existing.accepts_marketing = customer.accepts_marketing
            existing.orders_count = customer.orders_count
            existing.total_spent = customer.total_spent
            existing.currency_code = customer.currency_code
            existing.last_seen_at = datetime.now(tz=UTC)
        self._session.flush()
