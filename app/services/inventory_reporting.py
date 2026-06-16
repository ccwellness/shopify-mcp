"""InventoryReportingService — read-only inventory queries (TR-32).

`list_low_stock` is the centerpiece: it returns paginated InventoryLevel
rows whose `available` is below the supplied threshold. Levels with
`available IS NULL` are excluded — we cannot say something is low if we
do not know how much we have.

Cross-store queries are first-class: pass `store_ids=None` (or omit) to
sweep every store the connector knows about; pass an explicit tuple to
restrict the scan.
"""

from __future__ import annotations

from collections.abc import Callable

from app.domain.models import InventoryLevel, LocationId, Page, StoreId
from app.domain.repositories import UnitOfWork
from app.domain.specs import InventorySpec

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_LOW_STOCK_THRESHOLD = 10


def _clamp_limit(limit: int) -> int:
    return min(max(1, limit), MAX_LIMIT)


class InventoryReportingService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def list_low_stock(  # noqa: PLR0913 — kwargs-only; spec + paging are independent inputs
        self,
        *,
        store_ids: tuple[StoreId, ...] | None = None,
        threshold: int = DEFAULT_LOW_STOCK_THRESHOLD,
        location_id: LocationId | None = None,
        sku: str | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> Page[InventoryLevel]:
        if threshold < 0:
            raise ValueError("threshold must be non-negative")
        spec = InventorySpec(
            store_ids=store_ids,
            location_id=location_id,
            sku=sku,
            low_stock_threshold=threshold,
        )
        with self._uow_factory() as uow:
            return uow.inventory.list_levels(spec, limit=_clamp_limit(limit), cursor=cursor)

    def list_levels(  # noqa: PLR0913 — kwargs-only; spec + paging are independent inputs
        self,
        *,
        store_ids: tuple[StoreId, ...] | None = None,
        location_id: LocationId | None = None,
        sku: str | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> Page[InventoryLevel]:
        """All inventory levels matching the filter — no threshold gate.

        Companion to `list_low_stock` for "what do we have on hand" rather
        than "what's running out." Same paging contract.
        """
        spec = InventorySpec(
            store_ids=store_ids,
            location_id=location_id,
            sku=sku,
            low_stock_threshold=None,
        )
        with self._uow_factory() as uow:
            return uow.inventory.list_levels(spec, limit=_clamp_limit(limit), cursor=cursor)
