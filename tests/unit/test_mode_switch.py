"""Mode switch: live mode drives every MCP tool without ever building the engine.

The load-bearing guard monkeypatches `app.db.engine.get_engine` to raise; if the
container ever evaluated the DB branch of the `uow_factory` selector in live
mode, a tool call would blow up here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from dependency_injector import providers

import app.db.engine as eng
import mcp_server.tools  # noqa: F401 — registers tools on import
from app.config_mode import resolve_data_source
from app.container import Container
from app.domain.enums import SubscriptionProvider
from app.shopify.config import StoreConfig
from mcp_server.server import mcp, set_container_for_tests


def _money(amount: str) -> dict[str, Any]:
    return {"shopMoney": {"amount": amount}}


def _order_node(legacy: int, status: str, total: str) -> dict[str, Any]:
    return {
        "id": f"gid://shopify/Order/{legacy}",
        "legacyResourceId": str(legacy),
        "name": f"#{legacy}",
        "processedAt": "2026-06-01T00:00:00Z",
        "createdAt": "2026-06-01T00:00:00Z",
        "updatedAt": "2026-06-01T00:00:00Z",
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
                        "title": "W",
                        "sku": "S1",
                        "quantity": 1,
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


_PRODUCT_NODE = {
    "id": "gid://shopify/Product/99",
    "legacyResourceId": "99",
    "title": "Widget",
    "handle": "widget",
    "status": "ACTIVE",
    "vendor": "Acme",
    "productType": "G",
    "tags": [],
    "createdAt": "2026-01-01T00:00:00Z",
    "updatedAt": "2026-02-01T00:00:00Z",
    "variants": {
        "edges": [
            {
                "node": {
                    "id": "gid://shopify/ProductVariant/55",
                    "legacyResourceId": "55",
                    "title": "Default",
                    "sku": "S1",
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

_SHOPIFYQL = {
    "shopifyqlQuery": {
        "parseErrors": [],
        "tableData": {
            "columns": [
                {"name": "day"},
                {"name": "total_sales"},
                {"name": "orders"},
                {"name": "sessions"},
            ],
            "rows": [{"day": "2026-06-01", "total_sales": "200.00", "orders": 4, "sessions": 100}],
        },
    }
}

_INV_PAGE = {
    "productVariants": {
        "edges": [
            {
                "node": {
                    "inventoryItem": {
                        "id": "gid://shopify/InventoryItem/88",
                        "legacyResourceId": "88",
                        "sku": "S1",
                        "tracked": True,
                        "variant": {"legacyResourceId": "55"},
                        "inventoryLevels": {
                            "edges": [
                                {
                                    "node": {
                                        "location": {"legacyResourceId": "10"},
                                        "quantities": [{"name": "available", "quantity": 2}],
                                    }
                                }
                            ]
                        },
                    }
                }
            }
        ],
        "pageInfo": {"hasNextPage": False},
    }
}


def _route(query: str, variables: dict[str, Any] | None) -> dict[str, Any]:  # noqa: PLR0911
    if "shopifyqlQuery" in query:
        return _SHOPIFYQL
    if "productVariants(" in query:
        return _INV_PAGE
    if "refunds" in query and "orders(" in query:
        return {"orders": {"edges": [], "pageInfo": {"hasNextPage": False}}}
    if "orders(" in query:
        return {
            "orders": {
                "edges": [{"node": _order_node(1, "PAID", "100.00")}],
                "pageInfo": {"hasNextPage": False},
            }
        }
    if "order(id" in query:
        return {"order": _order_node(1, "PAID", "100.00")}
    if "products(" in query:
        return {
            "products": {"edges": [{"node": _PRODUCT_NODE}], "pageInfo": {"hasNextPage": False}}
        }
    if "product(id" in query:
        return {"product": _PRODUCT_NODE}
    return {}


class _RoutingClient:
    def query(
        self,
        store_key: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_mutation: bool = False,
    ) -> dict[str, Any]:  # noqa: ARG002
        return _route(query, variables)


@pytest.fixture
def live_mode_container(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MCP_DATA_SOURCE", "live")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Engine guard: any DB-branch evaluation will blow up here.
    eng.get_engine.cache_clear()
    eng.get_session_factory.cache_clear()

    def _boom() -> Any:
        raise AssertionError("engine built in live mode")

    monkeypatch.setattr(eng, "get_engine", _boom)

    configs = {
        "lubelife": StoreConfig(
            store_key="lubelife",
            shop_domain="lubelife.myshopify.com",
            client_id="",
            client_secret="",
            webhook_secret="",  # noqa: S106
            plus=False,
            subscription_provider=SubscriptionProvider.ORDERGROOVE,
            read_only=True,
            access_token="shpat_x",
            ordergroove_api_key="og-key",
        )
    }
    c = Container()
    c.store_configs.override(providers.Object(configs))
    c.shopify_client.override(providers.Object(_RoutingClient()))
    set_container_for_tests(c)
    try:
        yield None
    finally:
        set_container_for_tests(None)
        c.unwire()
        c.reset_override()


def _call(name: str, **args: object) -> Any:
    return asyncio.run(mcp.call_tool(name, args)).structured_content


def test_resolve_data_source_via_container() -> None:
    assert resolve_data_source({"DATABASE_URL": "x"}) == "db"
    assert resolve_data_source({}) == "live"
    assert resolve_data_source({"DATABASE_URL": "x", "MCP_DATA_SOURCE": "live"}) == "live"


def test_list_stores_live(live_mode_container: None) -> None:
    out = _call("list_stores")
    keys = [s["store_key"] for s in out["items"]]
    assert keys == ["lubelife"]


def test_list_and_get_order_live(live_mode_container: None) -> None:
    page = _call("list_orders", limit=10)
    assert page["items"][0]["id"] == 1
    got = _call("get_order", order_id=1)
    assert got["order"]["id"] == 1
    assert got["order"]["line_items"][0]["id"] == 101


def test_list_products_live(live_mode_container: None) -> None:
    page = _call("list_products", limit=10)
    assert page["items"][0]["id"] == 99
    assert page["items"][0]["variants"][0]["inventory_item_id"] == 88


def test_get_kpis_live(live_mode_container: None) -> None:
    out = _call("get_kpis", since="2026-06-01", until="2026-06-01")
    assert out["items"][0]["orders"] == 4
    assert out["items"][0]["conversion_rate"] == "0.0400"


def test_compare_stores_live(live_mode_container: None) -> None:
    out = _call("compare_stores", since="2026-06-01", until="2026-06-02")
    assert out["rows"][0]["paid_revenue"] == "100.00"


def test_check_inventory_live(live_mode_container: None) -> None:
    out = _call("check_inventory", sku="S1")
    assert out["items"][0]["available"] == 2


def test_list_subscriptions_live(
    live_mode_container: None, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    out = _call("list_subscriptions", limit=10)
    assert out["items"][0]["provider_contract_id"] == "PUB1"
