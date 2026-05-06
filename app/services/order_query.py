"""OrderQueryService — read-only access to the orders aggregate.

Thin service: takes a `UnitOfWork` factory, opens a UoW per call, dispatches
to the OrderRepository protocol. Does not import SQLAlchemy or any
concrete repository — the architecture tests in `tests/architecture/` enforce
that.

Limit handling: callers may request any positive `limit`, but it's clamped
to `MAX_LIMIT` server-side. This keeps a single bad client from asking for
millions of rows in one shot.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.domain.enums import FinancialStatus
from app.domain.models import Order, OrderId, Page, StoreId
from app.domain.repositories import UnitOfWork
from app.domain.specs import OrderSpec

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _clamp_limit(limit: int) -> int:
    return min(max(1, limit), MAX_LIMIT)


class OrderQueryService:
    """Read-only orders service. Wired by `app.container.Container`."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def list_orders(
        self,
        spec: OrderSpec,
        *,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> Page[Order]:
        with self._uow_factory() as uow:
            return uow.orders.find(spec, limit=_clamp_limit(limit), cursor=cursor)

    def get_order_by_id(self, order_id: OrderId) -> Order | None:
        with self._uow_factory() as uow:
            return uow.orders.get(order_id)

    def get_order_by_gid(self, store_id: StoreId, gid: str) -> Order | None:
        with self._uow_factory() as uow:
            return uow.orders.get_by_gid(store_id, gid)

    def count_orders_by_status(
        self,
        store_id: StoreId,
        since: datetime,
        until: datetime,
    ) -> dict[FinancialStatus, int]:
        with self._uow_factory() as uow:
            return uow.orders.count_by_status(store_id, since, until)
