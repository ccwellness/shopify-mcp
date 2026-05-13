"""ProductQueryService — read-only access to products + per-product analytics.

Composes ProductRepository (catalog), InventoryRepository (levels per variant),
OrderRepository (sales-by-day + recent orders for a product), and
LocationRepository (location name lookup for the inventory section).

The detail view bundles all four into a single composite via
`get_product_detail`, but the individual methods are exposed too so REST/
dashboard handlers can fan out as needed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.domain.models import (
    InventoryLevel,
    Location,
    Page,
    Product,
    ProductId,
    ProductOrderSummary,
    ProductSalesDay,
    StoreId,
    VariantId,
)
from app.domain.repositories import UnitOfWork
from app.domain.specs import ProductSpec

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_RECENT_ORDERS = 20


def _clamp_limit(limit: int) -> int:
    return min(max(1, limit), MAX_LIMIT)


class ProductQueryService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def list_products(
        self,
        spec: ProductSpec,
        *,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> Page[Product]:
        with self._uow_factory() as uow:
            return uow.products.find(spec, limit=_clamp_limit(limit), cursor=cursor)

    def get_product_by_id(self, product_id: ProductId) -> Product | None:
        with self._uow_factory() as uow:
            return uow.products.get(product_id)

    def get_inventory_for_variants(
        self, store_id: StoreId, variant_ids: tuple[VariantId, ...]
    ) -> tuple[InventoryLevel, ...]:
        with self._uow_factory() as uow:
            return uow.inventory.levels_for_variants(store_id, variant_ids)

    def get_sales_by_day(
        self,
        store_id: StoreId,
        product_id: ProductId,
        since: datetime,
        until: datetime,
    ) -> tuple[ProductSalesDay, ...]:
        with self._uow_factory() as uow:
            return uow.orders.sales_by_day_for_product(store_id, product_id, since, until)

    def get_recent_orders(
        self,
        store_id: StoreId,
        product_id: ProductId,
        *,
        limit: int = DEFAULT_RECENT_ORDERS,
    ) -> tuple[ProductOrderSummary, ...]:
        with self._uow_factory() as uow:
            orders = uow.orders.find_orders_containing_product(store_id, product_id, limit=limit)
        # Roll up units + SKUs of *this* product within each order. Done in
        # Python rather than SQL so the L4 layer doesn't have to ship a
        # bespoke aggregate type for one view.
        return tuple(
            ProductOrderSummary(
                order=o,
                units_of_product=sum(
                    li.quantity for li in o.line_items if li.product_id == product_id
                ),
                skus_of_product=tuple(
                    sorted(
                        {li.sku for li in o.line_items if li.product_id == product_id and li.sku}
                    )
                ),
            )
            for o in orders
        )

    def get_locations(self, store_id: StoreId) -> tuple[Location, ...]:
        with self._uow_factory() as uow:
            return uow.locations.list_for_store(store_id)
