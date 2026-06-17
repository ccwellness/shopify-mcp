"""Unit tests for the live (database-free) repositories.

Each repo is driven against a routing fake `ShopifyClient` that returns canned
GraphQL payloads chosen by inspecting the query text — no network, no DB.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import SubscriptionProvider
from app.domain.models import (
    ProductId,
    StoreId,
    SubscriptionContractId,
)
from app.domain.specs import AnalyticsWindowSpec, InventorySpec, OrderSpec, SubscriptionSpec
from app.services.store_compare import StoreComparisonService
from app.shopify.config import StoreConfig
from app.shopify.repositories import build_store_index
from app.shopify.repositories.analytics import LiveAnalyticsRepository
from app.shopify.repositories.inventory import LiveInventoryRepository
from app.shopify.repositories.orders import LiveOrderRepository
from app.shopify.repositories.products import LiveProductRepository
from app.shopify.repositories.subscriptions import LiveSubscriptionRepository
from app.shopify.repositories.unit_of_work import ShopifyUnitOfWork


class RoutingClient:
    """Fake ShopifyClient that routes `.query()` to a handler by query text."""

    def __init__(self, route: Callable[[str, dict[str, Any] | None], dict[str, Any]]) -> None:
        self._route = route
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def query(
        self,
        store_key: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_mutation: bool = False,  # noqa: ARG002
    ) -> dict[str, Any]:
        self.calls.append((store_key, query, variables))
        return self._route(query, variables)


def _index(*store_keys: str, og: str | None = None):
    configs = {
        k: StoreConfig(
            store_key=k,
            shop_domain=f"{k}.myshopify.com",
            client_id="cid",
            client_secret="csec",  # noqa: S106
            webhook_secret="wsec",  # noqa: S106
            plus=False,
            subscription_provider=SubscriptionProvider.ORDERGROOVE,
            read_only=True,
            access_token="shpat_x",
            ordergroove_api_key=og,
        )
        for k in store_keys
    }
    return build_store_index(configs)


def _money(amount: str) -> dict[str, Any]:
    return {"shopMoney": {"amount": amount}}


def _order_node(  # noqa: PLR0913
    legacy: int,
    *,
    status: str,
    total: str,
    qty: int,
    processed: str,
    with_customer: bool = False,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": f"gid://shopify/Order/{legacy}",
        "legacyResourceId": str(legacy),
        "name": f"#{legacy}",
        "email": "buyer@example.com",
        "processedAt": processed,
        "createdAt": processed,
        "updatedAt": processed,
        "currencyCode": "USD",
        "displayFinancialStatus": status,
        "displayFulfillmentStatus": "FULFILLED",
        "subtotalPriceSet": _money(total),
        "totalPriceSet": _money(total),
        "totalTaxSet": _money("0"),
        "totalDiscountsSet": _money("0"),
        "totalShippingPriceSet": _money("0"),
        "lineItems": {
            "edges": [
                {
                    "node": {
                        "id": f"gid://shopify/LineItem/{legacy}01",
                        "title": "Widget",
                        "sku": "SKU1",
                        "quantity": qty,
                        "variant": {"id": "gid://shopify/ProductVariant/55"},
                        "product": {"id": "gid://shopify/Product/99"},
                        "originalUnitPriceSet": _money(total),
                        "totalDiscountSet": _money("0"),
                        "requiresShipping": True,
                        "taxable": True,
                    }
                }
            ]
        },
    }
    if with_customer:
        node["customer"] = {
            "id": "gid://shopify/Customer/777",
            "legacyResourceId": "777",
            "email": "buyer@example.com",
            "numberOfOrders": 3,
            "createdAt": processed,
            "updatedAt": processed,
            "amountSpent": {"amount": "300.00", "currencyCode": "USD"},
        }
    return node


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def test_live_order_get_by_gid_normalizes_ids() -> None:
    node = _order_node(
        1001,
        status="PAID",
        total="50.00",
        qty=2,
        processed="2026-06-01T00:00:00Z",
        with_customer=True,
    )
    repo = LiveOrderRepository(RoutingClient(lambda q, v: {"order": node}), _index("lubelife"))
    order = repo.get_by_gid(StoreId(1), "gid://shopify/Order/1001")
    assert order is not None
    assert int(order.id) == 1001
    assert int(order.customer_id) == 777
    li = order.line_items[0]
    assert int(li.id) == 100101  # synthesized from the LineItem gid tail
    assert int(li.product_id) == 99
    assert int(li.variant_id) == 55


def test_live_order_aggregate_in_window() -> None:
    paid = _order_node(1, status="PAID", total="100.00", qty=3, processed="2026-06-01T12:00:00Z")
    refunded = _order_node(
        2, status="REFUNDED", total="40.00", qty=1, processed="2026-06-02T12:00:00Z"
    )
    page = {
        "orders": {
            "edges": [{"node": paid}, {"node": refunded}],
            "pageInfo": {"hasNextPage": False},
        }
    }
    repo = LiveOrderRepository(RoutingClient(lambda q, v: page), _index("lubelife"))
    agg = repo.aggregate_in_window(
        StoreId(1), datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 3, tzinfo=UTC)
    )
    assert agg.count == 2
    assert agg.revenue == Decimal("100.00")  # paid only
    assert agg.units == 3
    assert agg.currency_code == "USD"


def test_live_order_find_paginates_with_cursor() -> None:
    nodes = [
        _order_node(i, status="PAID", total="10.00", qty=1, processed=f"2026-06-0{i}T00:00:00Z")
        for i in (3, 2, 1)
    ]
    page = {
        "orders": {
            "edges": [{"node": n} for n in nodes],
            "pageInfo": {"hasNextPage": True, "endCursor": "abc"},
        }
    }
    repo = LiveOrderRepository(RoutingClient(lambda q, v: page), _index("lubelife"))
    result = repo.find(OrderSpec(), limit=2)
    assert len(result.items) == 2
    assert result.next_cursor is not None
    # newest first
    assert int(result.items[0].legacy_id) == 3


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def test_live_product_get_sets_ids() -> None:
    node = {
        "id": "gid://shopify/Product/99",
        "legacyResourceId": "99",
        "title": "Widget",
        "handle": "widget",
        "status": "ACTIVE",
        "vendor": "Acme",
        "productType": "Gadget",
        "tags": ["a", "b"],
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-02-01T00:00:00Z",
        "variants": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductVariant/55",
                        "legacyResourceId": "55",
                        "title": "Default",
                        "sku": "SKU1",
                        "price": "9.99",
                        "inventoryItem": {
                            "id": "gid://shopify/InventoryItem/88",
                            "legacyResourceId": "88",
                        },
                    }
                }
            ]
        },
    }
    repo = LiveProductRepository(RoutingClient(lambda q, v: {"product": node}), _index("lubelife"))
    product = repo.get(ProductId(99))
    assert product is not None
    assert int(product.id) == 99
    variant = product.variants[0]
    assert int(variant.id) == 55
    assert int(variant.inventory_item_id) == 88


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def test_live_inventory_low_stock_filters() -> None:
    def _inv_item(item_legacy: int, available: int) -> dict[str, Any]:
        return {
            "id": f"gid://shopify/InventoryItem/{item_legacy}",
            "legacyResourceId": str(item_legacy),
            "sku": "SKU1",
            "tracked": True,
            "variant": {"legacyResourceId": "55"},
            "inventoryLevels": {
                "edges": [
                    {
                        "node": {
                            "location": {"legacyResourceId": "10"},
                            "quantities": [{"name": "available", "quantity": available}],
                        }
                    }
                ]
            },
        }

    page = {
        "productVariants": {
            "edges": [
                {"node": {"inventoryItem": _inv_item(1, 3)}},
                {"node": {"inventoryItem": _inv_item(2, 50)}},
            ],
            "pageInfo": {"hasNextPage": False},
        }
    }
    repo = LiveInventoryRepository(RoutingClient(lambda q, v: page), _index("lubelife"))
    result = repo.list_levels(InventorySpec(low_stock_threshold=10), limit=50)
    assert len(result.items) == 1  # only available=3 is below threshold
    assert result.items[0].available == 3
    assert int(result.items[0].location_id) == 10


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def test_live_analytics_list_kpis_computes_conversion_and_aov() -> None:
    payload = {
        "shopifyqlQuery": {
            "parseErrors": [],
            "tableData": {
                "columns": [
                    {"name": "day", "dataType": "date"},
                    {"name": "total_sales", "dataType": "number"},
                    {"name": "orders", "dataType": "number"},
                    {"name": "sessions", "dataType": "number"},
                ],
                "rows": [
                    {"day": "2026-06-01", "total_sales": "200.00", "orders": 4, "sessions": 100},
                ],
            },
        }
    }
    repo = LiveAnalyticsRepository(RoutingClient(lambda q, v: payload), _index("lubelife"))
    rows = repo.list_kpis(AnalyticsWindowSpec(since=date(2026, 6, 1), until=date(2026, 6, 1)))
    assert len(rows) == 1
    kpi = rows[0]
    assert kpi.orders == 4
    assert kpi.sessions == 100
    assert kpi.revenue == Decimal("200.0000")
    assert kpi.conversion_rate == Decimal("0.0400")  # 4/100
    assert kpi.aov == Decimal("50.0000")  # 200/4


# ---------------------------------------------------------------------------
# Subscriptions (OrderGroove, monkeypatched client)
# ---------------------------------------------------------------------------


def test_live_subscriptions_find_and_get(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        {
            "public_id": "PUB1",
            "external_id": "gid://shopify/SubscriptionContract/501",
            "customer": "777",
            "every": 1,
            "every_period": 3,
            "live": True,
            "cancelled": None,
            "currency_code": "USD",
            "created": "2026-05-01 00:00:00",
            "updated": "2026-05-10 00:00:00",
        }
    ]
    monkeypatch.setattr(
        "app.shopify.repositories.subscriptions.OrderGrooveClient.iter_subscriptions",
        lambda self: iter(records),
    )
    repo = LiveSubscriptionRepository(_index("lubelife", og="og-key"))
    page = repo.find(SubscriptionSpec(), limit=50)
    assert len(page.items) == 1
    sub = page.items[0]
    assert sub.provider_contract_id == "PUB1"
    assert int(sub.legacy_id) == 501
    assert int(sub.customer_id) == 777
    # round-trip by synthetic id
    again = repo.get(SubscriptionContractId(int(sub.id)))
    assert again is not None
    assert again.provider_contract_id == "PUB1"


def test_live_subscriptions_skip_store_without_og_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.shopify.repositories.subscriptions.OrderGrooveClient.iter_subscriptions",
        lambda self: iter([]),
    )
    repo = LiveSubscriptionRepository(_index("shopjo", og=None))
    assert repo.find(SubscriptionSpec(), limit=50).items == ()


# ---------------------------------------------------------------------------
# Store comparison via the unchanged service + live UoW
# ---------------------------------------------------------------------------


def test_store_comparison_service_runs_on_live_uow() -> None:
    paid = _order_node(1, status="PAID", total="100.00", qty=2, processed="2026-06-01T12:00:00Z")
    orders_page = {"orders": {"edges": [{"node": paid}], "pageInfo": {"hasNextPage": False}}}

    def route(query: str, variables: dict[str, Any] | None) -> dict[str, Any]:
        if "refunds" in query:
            return {"orders": {"edges": [], "pageInfo": {"hasNextPage": False}}}
        return orders_page

    client = RoutingClient(route)
    index = _index("lubelife")
    uow = ShopifyUnitOfWork(client, index)
    svc = StoreComparisonService(uow_factory=lambda: uow)
    comparison = svc.compare_orders(
        since=datetime(2026, 6, 1, tzinfo=UTC), until=datetime(2026, 6, 2, tzinfo=UTC)
    )
    assert len(comparison.rows) == 1
    row = comparison.rows[0]
    assert row.store_key == "lubelife"
    assert row.paid_revenue == Decimal("100.00")
    assert row.net_revenue == Decimal("100.00")
    assert row.units_sold == 2
