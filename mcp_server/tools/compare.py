"""`compare_stores` MCP tool — the flagship cross-store rollup.

This is the Phase 4 acceptance gate: an LLM asks "compare last week's
revenue across all three stores" and the resolution chain runs
compare_stores → StoreComparisonService → Postgres. Numbers must match
`GET /api/v1/compare/orders`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.models import StoreId
from mcp_server.audit import audited
from mcp_server.dates import DateParseError, parse_datetime
from mcp_server.server import mcp, services


class StatusCountOut(BaseModel):
    status: str
    count: int


class StoreComparisonRowOut(BaseModel):
    store_id: int
    store_key: str
    order_count: int
    paid_revenue: str
    refunds_total: str
    net_revenue: str
    units_sold: int
    currency_code: str | None
    status_counts: list[StatusCountOut]


class CompareStoresOut(BaseModel):
    since: str
    until: str
    currency_warning: bool
    rows: list[StoreComparisonRowOut]


@mcp.tool
@audited("compare_stores")
def compare_stores(
    since: str = Field(
        description=(
            "Inclusive lower bound. ISO date / datetime, or 'yesterday', '7d', "
            "'last_week', 'last_month'."
        ),
    ),
    until: str = Field(
        description="Exclusive upper bound. Same format as `since`.",
    ),
    store_id: list[int] | None = Field(  # noqa: B008 — Pydantic Field-as-default is the idiom
        default=None,
        description="Optional store filter; omit for every active store.",
    ),
) -> CompareStoresOut:
    """Side-by-side per-store metrics over a window: order count, paid revenue,
    refunds in-window, net revenue (paid − refunds), units sold, status mix.

    If rows span more than one currency, `currency_warning=true` and cross-
    store totals should be reported with a caveat — the per-row values
    are still correct in their own currency.
    """
    try:
        since_dt = parse_datetime(since)
        until_dt = parse_datetime(until)
    except DateParseError as exc:
        raise ValueError(str(exc)) from exc
    if since_dt is None or until_dt is None:
        raise ValueError("since and until are required")

    result = services().compare.compare_orders(
        since=since_dt,
        until=until_dt,
        store_ids=tuple(StoreId(s) for s in store_id) if store_id else None,
    )
    return CompareStoresOut(
        since=since_dt.isoformat(),
        until=until_dt.isoformat(),
        currency_warning=result.currency_warning,
        rows=[
            StoreComparisonRowOut(
                store_id=int(r.store_id),
                store_key=r.store_key,
                order_count=r.order_count,
                paid_revenue=str(r.paid_revenue),
                refunds_total=str(r.refunds_total),
                net_revenue=str(r.net_revenue),
                units_sold=r.units_sold,
                currency_code=r.currency_code,
                status_counts=[
                    StatusCountOut(status=status.value, count=count)
                    for status, count in r.status_counts.items()
                ],
            )
            for r in result.rows
        ],
    )
