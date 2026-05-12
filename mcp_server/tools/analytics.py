"""`get_kpis` MCP tool — per (store, day) analytics rollups."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models import StoreId
from mcp_server.audit import audited
from mcp_server.dates import DateParseError, parse_date
from mcp_server.server import mcp, services


class KpiDayOut(BaseModel):
    store_id: int
    date: str  # YYYY-MM-DD
    sessions: int | None
    orders: int | None
    units: int | None
    revenue: str | None  # decimal as string
    conversion_rate: str | None  # decimal as string (e.g. "0.0250")
    aov: str | None  # decimal as string
    computed_at: datetime


class KpisOut(BaseModel):
    since: str
    until: str
    items: list[KpiDayOut]


@mcp.tool
@audited("get_kpis")
def get_kpis(
    since: str = Field(
        description=(
            "Inclusive lower bound. ISO date (YYYY-MM-DD) or relative phrase "
            "('yesterday', '7d', 'last_week', 'last_month')."
        ),
    ),
    until: str = Field(
        description="Inclusive upper bound. Same format as `since`.",
    ),
    store_id: list[int] | None = Field(  # noqa: B008 — Pydantic Field-as-default is the idiom
        default=None,
        description="Optional list of numeric store ids; omit for cross-store.",
    ),
) -> KpisOut:
    """Sessions, orders, units, revenue, conversion, and AOV per (store, day).

    Reads pre-computed rows from `analytics_kpi_daily`. To refresh
    numbers run `flask analytics compute` server-side; this tool never
    triggers a recompute.
    """
    try:
        since_d = parse_date(since)
        until_d = parse_date(until)
    except DateParseError as exc:
        raise ValueError(str(exc)) from exc
    if since_d is None or until_d is None:
        raise ValueError("since and until are required")

    rows = services().analytics.list_kpis(
        store_ids=tuple(StoreId(s) for s in store_id) if store_id else None,
        since=since_d,
        until=until_d,
    )
    return KpisOut(
        since=since_d.isoformat(),
        until=until_d.isoformat(),
        items=[
            KpiDayOut(
                store_id=int(r.store_id),
                date=r.date.isoformat(),
                sessions=r.sessions,
                orders=r.orders,
                units=r.units,
                revenue=str(r.revenue) if r.revenue is not None else None,
                conversion_rate=(str(r.conversion_rate) if r.conversion_rate is not None else None),
                aov=str(r.aov) if r.aov is not None else None,
                computed_at=r.computed_at,
            )
            for r in rows
        ],
    )
