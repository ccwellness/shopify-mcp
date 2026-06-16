"""FastMCP instance + lazy-built Container shared by every tool.

Tools import `mcp` to register themselves via `@mcp.tool`, and use the
`services()` accessor for typed handles to the L4 service layer. The
Container is built on first access so importing `mcp_server.server`
during tests doesn't touch the real DB.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastmcp import FastMCP

from app.container import Container
from app.services.analytics import AnalyticsService
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.inventory_reporting import InventoryReportingService
from app.services.order_query import OrderQueryService
from app.services.product_query import ProductQueryService
from app.services.store_compare import StoreComparisonService
from app.services.store_query import StoreQueryService
from app.services.subscription_query import SubscriptionQueryService
from app.services.sync import SyncService
from app.shopify.client import ShopifyClient
from app.shopify.config import StoreConfig

mcp = FastMCP("shopify-multistore")


@dataclass(frozen=True, slots=True)
class _Services:
    """Bundle of service handles tools need. Built once per process."""

    auth: AuthService
    audit: AuditService
    stores: StoreQueryService
    orders: OrderQueryService
    inventory: InventoryReportingService
    analytics: AnalyticsService
    compare: StoreComparisonService
    subscriptions: SubscriptionQueryService
    products: ProductQueryService
    sync: SyncService
    shopify: ShopifyClient
    store_configs: dict[str, StoreConfig]


_container_singleton: Container | None = None


def get_container() -> Container:
    """Return the process-wide Container, building it on first call."""
    global _container_singleton  # noqa: PLW0603 — module-level lazy singleton
    if _container_singleton is None:
        _container_singleton = Container()
    return _container_singleton


def set_container_for_tests(container: Container | None) -> None:
    """Test-only: override (or clear with None) the cached container."""
    global _container_singleton  # noqa: PLW0603 — module-level lazy singleton
    _container_singleton = container


def services() -> _Services:
    c = get_container()
    return _Services(
        auth=c.auth_service(),
        audit=c.audit_service(),
        stores=c.store_query_service(),
        orders=c.order_query_service(),
        inventory=c.inventory_reporting_service(),
        analytics=c.analytics_service(),
        compare=c.store_comparison_service(),
        subscriptions=c.subscription_query_service(),
        products=c.product_query_service(),
        sync=c.sync_service(),
        shopify=c.shopify_client(),
        store_configs=c.store_configs(),
    )
