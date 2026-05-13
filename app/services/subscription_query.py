"""SubscriptionQueryService — read-only access to subscription_contracts.

Thin L4 service mirroring `OrderQueryService`: takes a UoW factory, opens
a UoW per call, dispatches to the SubscriptionRepository protocol. No
SQLAlchemy imports — the architecture tests enforce that.
"""

from __future__ import annotations

from collections.abc import Callable

from app.domain.models import Page, SubscriptionContract
from app.domain.repositories import UnitOfWork
from app.domain.specs import SubscriptionSpec

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _clamp_limit(limit: int) -> int:
    return min(max(1, limit), MAX_LIMIT)


class SubscriptionQueryService:
    """Read-only subscriptions service. Wired by `app.container.Container`."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def list_subscriptions(
        self,
        spec: SubscriptionSpec,
        *,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> Page[SubscriptionContract]:
        with self._uow_factory() as uow:
            return uow.subscriptions.find(spec, limit=_clamp_limit(limit), cursor=cursor)
