"""SqlAlchemyAnalyticsRepository — concrete `AnalyticsRepository`.

Uses Postgres ON CONFLICT for upserts since `sessions_daily` and
`analytics_kpi_daily` have composite (store_id, date) primary keys —
the natural shape for the nightly analytics rollup writes.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.orm.analytics import AnalyticsKpiDayRow, SessionsDayRow
from app.domain.enums import AnalyticsSource
from app.domain.models import AnalyticsKpiDay, SessionsDay, StoreId
from app.domain.specs import AnalyticsWindowSpec


def _sessions_row_to_domain(row: SessionsDayRow) -> SessionsDay:
    return SessionsDay(
        store_id=StoreId(row.store_id),
        date=row.date,
        sessions=row.sessions,
        orders=row.orders,
        total_sales=row.total_sales,
        units_sold=row.units_sold,
        source=AnalyticsSource(row.source),
        pulled_at=row.pulled_at,
    )


def _kpi_row_to_domain(row: AnalyticsKpiDayRow) -> AnalyticsKpiDay:
    return AnalyticsKpiDay(
        store_id=StoreId(row.store_id),
        date=row.date,
        sessions=row.sessions,
        orders=row.orders,
        units=row.units,
        revenue=row.revenue,
        conversion_rate=row.conversion_rate,
        aov=row.aov,
        computed_at=row.computed_at,
    )


class SqlAlchemyAnalyticsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_sessions_day(self, store_id: StoreId, day: date) -> SessionsDay | None:
        row = self._session.get(SessionsDayRow, (int(store_id), day))
        return _sessions_row_to_domain(row) if row else None

    def upsert_sessions_day(self, row: SessionsDay) -> None:
        stmt = pg_insert(SessionsDayRow).values(
            store_id=int(row.store_id),
            date=row.date,
            sessions=row.sessions,
            orders=row.orders,
            total_sales=row.total_sales,
            units_sold=row.units_sold,
            source=row.source.value,
            pulled_at=row.pulled_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["store_id", "date"],
            set_={
                "sessions": stmt.excluded.sessions,
                "orders": stmt.excluded.orders,
                "total_sales": stmt.excluded.total_sales,
                "units_sold": stmt.excluded.units_sold,
                "source": stmt.excluded.source,
                "pulled_at": stmt.excluded.pulled_at,
            },
        )
        self._session.execute(stmt)
        self._session.flush()

    def list_sessions(self, spec: AnalyticsWindowSpec) -> tuple[SessionsDay, ...]:
        stmt = select(SessionsDayRow).where(
            SessionsDayRow.date >= spec.since,
            SessionsDayRow.date <= spec.until,
        )
        if spec.store_ids is not None:
            stmt = stmt.where(SessionsDayRow.store_id.in_([int(s) for s in spec.store_ids]))
        stmt = stmt.order_by(SessionsDayRow.store_id, SessionsDayRow.date)
        rows = self._session.scalars(stmt).all()
        return tuple(_sessions_row_to_domain(r) for r in rows)

    def get_kpi_day(self, store_id: StoreId, day: date) -> AnalyticsKpiDay | None:
        row = self._session.get(AnalyticsKpiDayRow, (int(store_id), day))
        return _kpi_row_to_domain(row) if row else None

    def upsert_kpi_day(self, row: AnalyticsKpiDay) -> None:
        stmt = pg_insert(AnalyticsKpiDayRow).values(
            store_id=int(row.store_id),
            date=row.date,
            sessions=row.sessions,
            orders=row.orders,
            units=row.units,
            revenue=row.revenue,
            conversion_rate=row.conversion_rate,
            aov=row.aov,
            computed_at=row.computed_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["store_id", "date"],
            set_={
                "sessions": stmt.excluded.sessions,
                "orders": stmt.excluded.orders,
                "units": stmt.excluded.units,
                "revenue": stmt.excluded.revenue,
                "conversion_rate": stmt.excluded.conversion_rate,
                "aov": stmt.excluded.aov,
                "computed_at": stmt.excluded.computed_at,
            },
        )
        self._session.execute(stmt)
        self._session.flush()

    def list_kpis(self, spec: AnalyticsWindowSpec) -> tuple[AnalyticsKpiDay, ...]:
        stmt = select(AnalyticsKpiDayRow).where(
            AnalyticsKpiDayRow.date >= spec.since,
            AnalyticsKpiDayRow.date <= spec.until,
        )
        if spec.store_ids is not None:
            stmt = stmt.where(AnalyticsKpiDayRow.store_id.in_([int(s) for s in spec.store_ids]))
        stmt = stmt.order_by(AnalyticsKpiDayRow.store_id, AnalyticsKpiDayRow.date)
        rows = self._session.scalars(stmt).all()
        return tuple(_kpi_row_to_domain(r) for r in rows)
