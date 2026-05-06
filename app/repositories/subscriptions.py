"""SqlAlchemySubscriptionRepository — concrete `SubscriptionRepository`."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import literal, select, tuple_
from sqlalchemy.orm import Session

from app.db.orm.subscription import SubscriptionContractRow
from app.domain.enums import SubscriptionProvider, SubscriptionStatus
from app.domain.models import (
    CustomerId,
    Page,
    StoreId,
    SubscriptionContract,
    SubscriptionContractId,
)
from app.domain.specs import SubscriptionSpec
from app.repositories._cursor import decode, encode


def _row_to_domain(row: SubscriptionContractRow) -> SubscriptionContract:
    return SubscriptionContract(
        id=SubscriptionContractId(row.id),
        store_id=StoreId(row.store_id),
        customer_id=CustomerId(row.customer_id) if row.customer_id is not None else None,
        provider=SubscriptionProvider(row.provider),
        provider_contract_id=row.provider_contract_id,
        gid=row.gid,
        legacy_id=row.legacy_id,
        status=SubscriptionStatus(row.status),
        next_billing_date=row.next_billing_date,
        frequency_interval=row.frequency_interval,
        frequency_count=row.frequency_count,
        currency_code=row.currency_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemySubscriptionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, contract_id: SubscriptionContractId) -> SubscriptionContract | None:
        row = self._session.get(SubscriptionContractRow, int(contract_id))
        return _row_to_domain(row) if row else None

    def find(
        self,
        spec: SubscriptionSpec,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[SubscriptionContract]:
        stmt = select(SubscriptionContractRow)
        if spec.store_ids is not None:
            stmt = stmt.where(
                SubscriptionContractRow.store_id.in_([int(s) for s in spec.store_ids])
            )
        if spec.customer_id is not None:
            stmt = stmt.where(SubscriptionContractRow.customer_id == int(spec.customer_id))
        if spec.status is not None:
            stmt = stmt.where(SubscriptionContractRow.status == spec.status.value)
        if spec.provider is not None:
            stmt = stmt.where(SubscriptionContractRow.provider == spec.provider.value)
        if cursor:
            cur_updated_at, cur_id = decode(cursor)
            stmt = stmt.where(
                tuple_(SubscriptionContractRow.updated_at, SubscriptionContractRow.id)
                < tuple_(literal(cur_updated_at), literal(cur_id))
            )
        stmt = stmt.order_by(
            SubscriptionContractRow.updated_at.desc(), SubscriptionContractRow.id.desc()
        ).limit(limit + 1)
        rows = self._session.scalars(stmt).all()
        items = [_row_to_domain(r) for r in rows[:limit]]
        next_cursor = (
            encode(rows[limit - 1].updated_at, rows[limit - 1].id) if len(rows) > limit else None
        )
        return Page(items=tuple(items), next_cursor=next_cursor)

    def upsert(self, contract: SubscriptionContract) -> None:
        existing = self._session.scalar(
            select(SubscriptionContractRow).where(
                SubscriptionContractRow.store_id == int(contract.store_id),
                SubscriptionContractRow.provider == contract.provider.value,
                SubscriptionContractRow.provider_contract_id == contract.provider_contract_id,
            )
        )
        if existing is None:
            self._session.add(
                SubscriptionContractRow(
                    store_id=int(contract.store_id),
                    customer_id=(
                        int(contract.customer_id) if contract.customer_id is not None else None
                    ),
                    provider=contract.provider.value,
                    provider_contract_id=contract.provider_contract_id,
                    gid=contract.gid,
                    legacy_id=contract.legacy_id,
                    status=contract.status.value,
                    next_billing_date=contract.next_billing_date,
                    frequency_interval=contract.frequency_interval,
                    frequency_count=contract.frequency_count,
                    currency_code=contract.currency_code,
                )
            )
        else:
            existing.customer_id = (
                int(contract.customer_id) if contract.customer_id is not None else None
            )
            existing.gid = contract.gid
            existing.legacy_id = contract.legacy_id
            existing.status = contract.status.value
            existing.next_billing_date = contract.next_billing_date
            existing.frequency_interval = contract.frequency_interval
            existing.frequency_count = contract.frequency_count
            existing.currency_code = contract.currency_code
            existing.last_seen_at = datetime.now(tz=UTC)
        self._session.flush()
