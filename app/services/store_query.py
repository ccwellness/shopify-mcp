"""StoreQueryService — read-only access to the Store aggregate.

Tiny — one method today (`list_active`). Future home for `get_by_key` and
the `GET /api/v1/stores` REST surface called out in the design doc. Lives
as its own service so the dashboard, GraphQL gateway, and REST API all
share a single layer-clean accessor for the store directory.
"""

from __future__ import annotations

from collections.abc import Callable

from app.domain.models import Store
from app.domain.repositories import UnitOfWork


class StoreQueryService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def list_active(self) -> tuple[Store, ...]:
        """Active stores sorted by `store_key` for stable display."""
        with self._uow_factory() as uow:
            return tuple(sorted(uow.stores.list_active(), key=lambda s: s.store_key))
