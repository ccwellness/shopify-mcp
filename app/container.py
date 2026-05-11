"""Dependency-injection container — composition root for the app (TR-21).

Production wiring goes through `Container()` in `create_app()`. Tests
build their own `Container` and `.override()` providers to swap the
SQLAlchemy `UnitOfWork` for `InMemoryUnitOfWork`, the `JobQueue` for an
in-memory recorder, etc. (TR-42).

Why a container instead of inline construction in the app factory:

- Single source of truth for how each layer is wired. Services keep
  their pure-Python constructors; the container knows how to fulfill
  them.
- Tests substitute fakes by overriding *providers*, not by monkey-
  patching modules.
- Phase-3 services (analytics, subscriptions) plug in here without
  touching `create_app()` or webhook routes.
"""

from __future__ import annotations

from collections.abc import Callable

from dependency_injector import containers, providers
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import get_session_factory
from app.db.unit_of_work import SqlAlchemyUnitOfWork
from app.domain.repositories import UnitOfWork
from app.jobs.queue import InlineJobQueue
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.inventory_reporting import InventoryReportingService
from app.services.order_query import OrderQueryService
from app.services.store_compare import StoreComparisonService
from app.services.store_query import StoreQueryService
from app.services.sync import SyncService
from app.services.webhook_ingest import WebhookIngestService
from app.shopify.bulk import BulkOperationsClient
from app.shopify.client import ShopifyClient
from app.shopify.config import load_store_configs


def _build_uow_factory(
    session_factory: sessionmaker[Session],
) -> Callable[[], UnitOfWork]:
    """Wrap a sessionmaker into the `() -> UnitOfWork` callable services consume."""

    def factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return factory


class Container(containers.DeclarativeContainer):
    """Composition root. See module docstring."""

    config = providers.Configuration()

    # ---- Configuration / per-store credentials ---------------------------
    store_configs = providers.Singleton(load_store_configs)

    # ---- L1 Infrastructure ----------------------------------------------
    session_factory = providers.Singleton(get_session_factory)

    # uow_factory is itself a callable: `container.uow_factory()` returns
    # the `() -> UnitOfWork` lambda services hold onto. Singleton means the
    # same lambda is reused, but each call to it produces a fresh UoW.
    uow_factory = providers.Singleton(
        _build_uow_factory,
        session_factory=session_factory,
    )

    job_queue = providers.Singleton(InlineJobQueue)

    shopify_client = providers.Singleton(ShopifyClient, store_configs)
    bulk_client = providers.Singleton(BulkOperationsClient, shopify_client)

    # ---- L4 Services -----------------------------------------------------
    auth_service = providers.Factory(
        AuthService,
        uow_factory=uow_factory,
    )

    audit_service = providers.Factory(
        AuditService,
        uow_factory=uow_factory,
    )

    order_query_service = providers.Factory(
        OrderQueryService,
        uow_factory=uow_factory,
    )

    inventory_reporting_service = providers.Factory(
        InventoryReportingService,
        uow_factory=uow_factory,
    )

    store_comparison_service = providers.Factory(
        StoreComparisonService,
        uow_factory=uow_factory,
    )

    store_query_service = providers.Factory(
        StoreQueryService,
        uow_factory=uow_factory,
    )

    webhook_ingest_service = providers.Factory(
        WebhookIngestService,
        uow_factory=uow_factory,
        store_configs=store_configs,
        job_queue=job_queue,
    )

    sync_service = providers.Factory(
        SyncService,
        uow_factory=uow_factory,
        shopify_client=shopify_client,
        bulk_client=bulk_client,
        store_configs=store_configs,
    )
