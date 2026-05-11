"""SqlAlchemyRefundRepository — concrete `RefundRepository`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.orm.refund import RefundRow
from app.domain.models import Money, OrderId, Refund, RefundId, StoreId


def _to_domain(row: RefundRow) -> Refund:
    return Refund(
        id=RefundId(row.id),
        store_id=StoreId(row.store_id),
        order_id=OrderId(row.order_id),
        gid=row.gid,
        legacy_id=row.legacy_id,
        amount=row.amount,
        currency_code=row.currency_code,
        note=row.note,
        created_at=row.created_at,
    )


class SqlAlchemyRefundRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_gid(self, store_id: StoreId, gid: str) -> Refund | None:
        row = self._session.scalar(
            select(RefundRow).where(RefundRow.store_id == int(store_id), RefundRow.gid == gid)
        )
        return _to_domain(row) if row is not None else None

    def list_for_order(self, order_id: OrderId) -> tuple[Refund, ...]:
        rows = self._session.scalars(
            select(RefundRow)
            .where(RefundRow.order_id == int(order_id))
            .order_by(RefundRow.created_at)
        ).all()
        return tuple(_to_domain(r) for r in rows)

    def list_in_window(
        self, store_id: StoreId, since: datetime, until: datetime
    ) -> tuple[Refund, ...]:
        rows = self._session.scalars(
            select(RefundRow)
            .where(
                RefundRow.store_id == int(store_id),
                RefundRow.created_at >= since,
                RefundRow.created_at < until,
            )
            .order_by(RefundRow.created_at)
        ).all()
        return tuple(_to_domain(r) for r in rows)

    def sum_in_window(self, store_id: StoreId, since: datetime, until: datetime) -> Money:
        total = self._session.scalar(
            select(func.coalesce(func.sum(RefundRow.amount), Decimal("0"))).where(
                RefundRow.store_id == int(store_id),
                RefundRow.created_at >= since,
                RefundRow.created_at < until,
            )
        )
        return Decimal(total) if total is not None else Decimal("0")

    def upsert(self, refund: Refund) -> RefundId:
        existing = self._session.scalar(
            select(RefundRow).where(
                RefundRow.store_id == int(refund.store_id), RefundRow.gid == refund.gid
            )
        )
        if existing is None:
            row = RefundRow(
                store_id=int(refund.store_id),
                order_id=int(refund.order_id),
                gid=refund.gid,
                legacy_id=refund.legacy_id,
                amount=refund.amount,
                currency_code=refund.currency_code,
                note=refund.note,
                created_at=refund.created_at,
            )
            self._session.add(row)
            self._session.flush()
            return RefundId(row.id)
        existing.order_id = int(refund.order_id)
        existing.legacy_id = refund.legacy_id
        existing.amount = refund.amount
        existing.currency_code = refund.currency_code
        existing.note = refund.note
        existing.created_at = refund.created_at
        self._session.flush()
        return RefundId(existing.id)
