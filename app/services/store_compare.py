"""StoreComparisonService — cross-store rollups for a `[since, until]` window.

Builds on `OrderRepository.aggregate_in_window` for paid revenue + units +
status mix, and `RefundRepository.sum_in_window` to net refunds out of the
same window. The result is one `StoreComparisonRow` per active store
(optionally filtered by `store_ids`), wrapped in a `StoreComparison` that
flags multi-currency results.

This service is the backend for the future `/api/v1/compare/*` REST surface
and the Phase 4 MCP `compare_stores` tool — both share these numbers so
the dashboard and the LLM agree.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from app.domain.models import (
    StoreComparison,
    StoreComparisonRow,
    StoreId,
)
from app.domain.repositories import UnitOfWork


class StoreComparisonService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def compare_orders(
        self,
        *,
        since: datetime,
        until: datetime,
        store_ids: tuple[StoreId, ...] | None = None,
    ) -> StoreComparison:
        """Per-store order rollup with refund netting over `[since, until)`.

        `store_ids=None` includes every active store. An explicit tuple
        restricts the comparison to those stores; unknown ids are silently
        dropped (the corresponding store simply does not appear in `rows`).
        """
        if since >= until:
            raise ValueError("since must be strictly before until")

        with self._uow_factory() as uow:
            stores = uow.stores.list_active()
            if store_ids is not None:
                allowed = set(store_ids)
                stores = tuple(s for s in stores if s.id in allowed)
            # Stable, human-friendly ordering — by store_key — so dashboard
            # columns don't reshuffle between calls.
            stores = tuple(sorted(stores, key=lambda s: s.store_key))

            rows: list[StoreComparisonRow] = []
            for store in stores:
                agg = uow.orders.aggregate_in_window(store.id, since, until)
                refunds_total = uow.refunds.sum_in_window(store.id, since, until)
                rows.append(
                    StoreComparisonRow(
                        store_id=store.id,
                        store_key=store.store_key,
                        order_count=agg.count,
                        paid_revenue=agg.revenue,
                        refunds_total=refunds_total,
                        net_revenue=agg.revenue - refunds_total,
                        units_sold=agg.units,
                        currency_code=agg.currency_code,
                        status_counts=agg.status_counts,
                    )
                )

        currencies = {r.currency_code for r in rows if r.currency_code is not None}
        return StoreComparison(
            since=since,
            until=until,
            rows=tuple(rows),
            currency_warning=len(currencies) > 1,
        )

    def total_net_revenue(self, comparison: StoreComparison) -> Decimal:
        """Sum of `net_revenue` across rows. Caller should check
        `comparison.currency_warning` first — summing across currencies is
        a category error, so this raises if the warning is set."""
        if comparison.currency_warning:
            raise ValueError("Cannot total net_revenue across rows with mixed currencies")
        return sum((r.net_revenue for r in comparison.rows), Decimal("0"))
