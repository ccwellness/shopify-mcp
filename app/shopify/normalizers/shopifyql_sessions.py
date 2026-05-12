"""ShopifyQL `sessions` rows → domain `SessionsDay` records.

The ShopifyQL query we run on every store (TR-29):

    FROM sales, sessions
    SHOW day, total_sales, orders, sessions
    GROUP BY day
    SINCE -<N>d UNTIL -1d

Phase 0 confirmed this returns: `day` (date), `total_sales` (decimal),
`orders` (int), `sessions` (int). `units_sold` is NOT in this query
shape — it comes from the orders aggregate instead, so we leave it
None here and let the KPI service fold it in.

Missing/null cells fall through to None — better to record a partial
day than reject the whole pull because one metric is suppressed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.enums import AnalyticsSource
from app.domain.models import SessionsDay, StoreId
from app.shopify.shopifyql import ShopifyqlResult


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    """ShopifyQL emits `day` as 'YYYY-MM-DD'. Accept date objects too."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def normalize_shopifyql_sessions(
    store_id: StoreId,
    result: ShopifyqlResult,
    *,
    pulled_at: datetime | None = None,
) -> tuple[SessionsDay, ...]:
    """Decode `result.rows` into one `SessionsDay` per day.

    Rows whose `day` is missing/unparseable are dropped — without a date
    we have no primary key. All other fields tolerate None.
    """
    if not result.rows:
        return ()

    when = pulled_at or datetime.now(tz=UTC)
    day_idx = result.index_of("day")
    sales_idx = _maybe_index(result, "total_sales")
    orders_idx = _maybe_index(result, "orders")
    sessions_idx = _maybe_index(result, "sessions")

    out: list[SessionsDay] = []
    for row in result.rows:
        day = _to_date(row[day_idx])
        if day is None:
            continue
        out.append(
            SessionsDay(
                store_id=store_id,
                date=day,
                sessions=_to_int(row[sessions_idx]) if sessions_idx is not None else None,
                orders=_to_int(row[orders_idx]) if orders_idx is not None else None,
                total_sales=_to_decimal(row[sales_idx]) if sales_idx is not None else None,
                units_sold=None,  # not in this query shape; KPI service folds it in
                source=AnalyticsSource.SHOPIFYQL,
                pulled_at=when,
            )
        )
    return tuple(out)


def _maybe_index(result: ShopifyqlResult, column_name: str) -> int | None:
    """Return the column index if present, else None — for optional columns."""
    try:
        return result.index_of(column_name)
    except KeyError:
        return None
