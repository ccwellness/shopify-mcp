"""SqlAlchemy implementation of the `UnitOfWork` Protocol (TR-23).

Holds a Session for the duration of a `with uow:` block. Repositories
are lazily attached to the session on first access. Commit and rollback
are explicit — the context manager only auto-rollbacks on uncaught
exception.

Typical use from a service:

    def list_orders(self, ...):
        with self._uow_factory() as uow:
            return uow.orders.find(spec, limit=50)
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from app.domain.repositories import (
    AnalyticsRepository,
    ApiAuditLogRepository,
    ApiTokenRepository,
    CustomerRepository,
    InventoryRepository,
    LocationRepository,
    OrderRepository,
    ProductRepository,
    StoreRepository,
    SubscriptionRepository,
    SyncStateRepository,
    WebhookEventLogRepository,
)
from app.repositories.analytics import SqlAlchemyAnalyticsRepository
from app.repositories.api_audit_log import SqlAlchemyApiAuditLogRepository
from app.repositories.api_tokens import SqlAlchemyApiTokenRepository
from app.repositories.customers import SqlAlchemyCustomerRepository
from app.repositories.inventory import SqlAlchemyInventoryRepository
from app.repositories.locations import SqlAlchemyLocationRepository
from app.repositories.orders import SqlAlchemyOrderRepository
from app.repositories.products import SqlAlchemyProductRepository
from app.repositories.stores import SqlAlchemyStoreRepository
from app.repositories.subscriptions import SqlAlchemySubscriptionRepository
from app.repositories.sync_state import SqlAlchemySyncStateRepository
from app.repositories.webhook_events import SqlAlchemyWebhookEventLogRepository


class SqlAlchemyUnitOfWork:
    """Concrete `UnitOfWork` backed by a SQLAlchemy session."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        # Attribute types are declared as the *Protocols* — the concretes
        # below structurally satisfy them. This is what lets a UoW returned
        # from this class type-check as `UnitOfWork` (the Protocol) wherever
        # services/views consume it.
        self.stores: StoreRepository
        self.locations: LocationRepository
        self.customers: CustomerRepository
        self.orders: OrderRepository
        self.products: ProductRepository
        self.inventory: InventoryRepository
        self.subscriptions: SubscriptionRepository
        self.analytics: AnalyticsRepository
        self.sync_state: SyncStateRepository
        self.webhook_events: WebhookEventLogRepository
        self.api_tokens: ApiTokenRepository
        self.api_audit_log: ApiAuditLogRepository

    def __enter__(self) -> Self:
        self._session = self._session_factory()
        self.stores = SqlAlchemyStoreRepository(self._session)
        self.locations = SqlAlchemyLocationRepository(self._session)
        self.customers = SqlAlchemyCustomerRepository(self._session)
        self.orders = SqlAlchemyOrderRepository(self._session)
        self.products = SqlAlchemyProductRepository(self._session)
        self.inventory = SqlAlchemyInventoryRepository(self._session)
        self.subscriptions = SqlAlchemySubscriptionRepository(self._session)
        self.analytics = SqlAlchemyAnalyticsRepository(self._session)
        self.sync_state = SqlAlchemySyncStateRepository(self._session)
        self.webhook_events = SqlAlchemyWebhookEventLogRepository(self._session)
        self.api_tokens = SqlAlchemyApiTokenRepository(self._session)
        self.api_audit_log = SqlAlchemyApiAuditLogRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        try:
            if exc_type is not None:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("commit() called outside of a `with uow:` block")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is None:
            raise RuntimeError("rollback() called outside of a `with uow:` block")
        self._session.rollback()
