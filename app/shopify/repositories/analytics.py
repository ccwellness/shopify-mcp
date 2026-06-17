"""Live AnalyticsRepository — computes KPIs from ShopifyQL in real time.

There is no precomputed `analytics_kpi_daily` table in live mode, so
`list_kpis` runs the same ShopifyQL `sales, sessions` query the nightly sync
uses, but over an explicit date window, and folds each day into an
`AnalyticsKpiDay` on the fly. `units` is left None (the ShopifyQL shape we run
doesn't carry it — same convention as the sessions normalizer).

Sync-only methods (sessions/kpi upserts, single-day reads) are not part of the
live read path and return empty/None.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.domain.models import AnalyticsKpiDay, SessionsDay, StoreId
from app.domain.specs import AnalyticsWindowSpec
from app.shopify.normalizers.shopifyql_sessions import normalize_shopifyql_sessions
from app.shopify.repositories._base import _LiveRepo
from app.shopify.shopifyql import run_shopifyql

_CONVERSION_QUANT = Decimal("0.0001")
_MONEY_QUANT = Decimal("0.0001")


def _conversion_rate(orders: int | None, sessions: int | None) -> Decimal | None:
    if not orders or not sessions:
        return None
    return (Decimal(orders) / Decimal(sessions)).quantize(_CONVERSION_QUANT, rounding=ROUND_HALF_UP)


def _aov(revenue: Decimal | None, orders: int | None) -> Decimal | None:
    if not orders or revenue is None:
        return None
    return (revenue / Decimal(orders)).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _kpi_query(since: date, until: date) -> str:
    return (
        "FROM sales, sessions "
        "SHOW day, total_sales, orders, sessions "
        "GROUP BY day "
        f"SINCE {since.isoformat()} UNTIL {until.isoformat()}"
    )


class LiveAnalyticsRepository(_LiveRepo):
    def list_kpis(self, spec: AnalyticsWindowSpec) -> tuple[AnalyticsKpiDay, ...]:
        keys = self._index.resolve_keys(spec.store_ids)
        now = datetime.now(tz=UTC)
        rows: list[AnalyticsKpiDay] = []
        for key in keys:
            store_id = self._index.id_for(key)
            if store_id is None:
                continue
            result = run_shopifyql(self._client, key, _kpi_query(spec.since, spec.until))
            for sd in normalize_shopifyql_sessions(store_id, result, pulled_at=now):
                if sd.date < spec.since or sd.date > spec.until:
                    continue
                rows.append(_to_kpi(sd, now))
        rows.sort(key=lambda r: (r.date, int(r.store_id)))
        return tuple(rows)

    # -- not on the live read path ----------------------------------------

    def get_sessions_day(self, store_id: StoreId, day: date) -> SessionsDay | None:  # noqa: ARG002
        return None

    def upsert_sessions_day(self, row: SessionsDay) -> None:  # noqa: ARG002
        return None

    def list_sessions(self, spec: AnalyticsWindowSpec) -> tuple[SessionsDay, ...]:
        keys = self._index.resolve_keys(spec.store_ids)
        now = datetime.now(tz=UTC)
        out: list[SessionsDay] = []
        for key in keys:
            store_id = self._index.id_for(key)
            if store_id is None:
                continue
            result = run_shopifyql(self._client, key, _kpi_query(spec.since, spec.until))
            out.extend(
                sd
                for sd in normalize_shopifyql_sessions(store_id, result, pulled_at=now)
                if spec.since <= sd.date <= spec.until
            )
        return tuple(out)

    def get_kpi_day(self, store_id: StoreId, day: date) -> AnalyticsKpiDay | None:  # noqa: ARG002
        return None

    def upsert_kpi_day(self, row: AnalyticsKpiDay) -> None:  # noqa: ARG002
        return None


def _to_kpi(sd: SessionsDay, computed_at: datetime) -> AnalyticsKpiDay:
    revenue = sd.total_sales
    return AnalyticsKpiDay(
        store_id=sd.store_id,
        date=sd.date,
        sessions=sd.sessions,
        orders=sd.orders,
        units=sd.units_sold,
        revenue=(revenue.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP) if revenue else revenue),
        conversion_rate=_conversion_rate(sd.orders, sd.sessions),
        aov=_aov(revenue, sd.orders),
        computed_at=computed_at,
    )
