"""Unit tests for the live-fetch MCP tools.

Drives each tool through `mcp.call_tool` so the Pydantic argument coercion
runs exactly as it would for an LLM caller. The Shopify GraphQL client is
replaced with a `FakeShopifyClient` that records calls and returns canned
payloads; OrderGroove is patched at the constructor level for the same
effect without hitting the network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from dependency_injector import providers

import mcp_server.tools  # noqa: F401 — registers tools on import
from app.container import Container
from app.domain.enums import SubscriptionProvider
from app.domain.repositories import UnitOfWork
from app.shopify.config import StoreConfig
from app.shopify.errors import ShopifyError
from mcp_server.server import mcp, set_container_for_tests

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeShopifyClient:
    """Records every `.query()` call and returns whichever canned payload
    matches the current call index — keeps tests deterministic without
    mocking httpx."""

    def __init__(self, payloads: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._payloads = payloads or [{}]
        self._error_on_call: Exception | None = None

    def set_error(self, exc: Exception) -> None:
        self._error_on_call = exc

    def query(
        self,
        store_key: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_mutation: bool = False,  # noqa: ARG002 — match real signature
    ) -> dict[str, Any]:
        self.calls.append((store_key, query, variables))
        if self._error_on_call is not None:
            raise self._error_on_call
        idx = min(len(self.calls) - 1, len(self._payloads) - 1)
        return self._payloads[idx]


def _store_config(store_key: str, *, og_key: str | None = None) -> StoreConfig:
    return StoreConfig(
        store_key=store_key,
        shop_domain=f"{store_key}.myshopify.com",
        client_id="cid",
        client_secret="csec",  # noqa: S106 — test placeholder, not a real secret
        webhook_secret="wsec",  # noqa: S106 — test placeholder, not a real secret
        plus=False,
        subscription_provider=SubscriptionProvider.UNKNOWN,
        read_only=True,
        ordergroove_api_key=og_key,
        ordergroove_public_id=None,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_shopify() -> FakeShopifyClient:
    return FakeShopifyClient()


@pytest.fixture
def live_container(
    fake_uow_factory: Callable[[], UnitOfWork],
    fake_shopify: FakeShopifyClient,
) -> Iterator[Container]:
    """Container with two registered stores (lubelife has OG creds, shopjo doesn't)
    plus the fake Shopify client wired in."""
    configs = {
        "lubelife": _store_config("lubelife", og_key="og-test-key"),
        "shopjo": _store_config("shopjo", og_key=None),
    }
    c = Container()
    c.uow_factory.override(providers.Object(fake_uow_factory))
    c.store_configs.override(providers.Object(configs))
    c.shopify_client.override(providers.Object(fake_shopify))
    set_container_for_tests(c)
    try:
        yield c
    finally:
        set_container_for_tests(None)
        c.unwire()
        c.reset_override()


def _call(name: str, **args: object) -> Any:
    return asyncio.run(mcp.call_tool(name, args)).structured_content


# ---------------------------------------------------------------------------
# Shopify live tools — orders
# ---------------------------------------------------------------------------


def test_live_get_order_returns_node(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    fake_shopify._payloads = [{"order": {"id": "gid://shopify/Order/1", "name": "#1001"}}]
    out = _call(
        "shopify_live_get_order",
        store_key="lubelife",
        order_gid="gid://shopify/Order/1",
    )
    assert out["store_key"] == "lubelife"
    assert out["order"]["name"] == "#1001"
    # The GraphQL call was made with the gid variable.
    assert fake_shopify.calls[0][2] == {"id": "gid://shopify/Order/1"}


def test_live_get_order_null_when_missing(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    fake_shopify._payloads = [{"order": None}]
    out = _call(
        "shopify_live_get_order",
        store_key="lubelife",
        order_gid="gid://shopify/Order/999",
    )
    assert out["order"] is None


def test_live_list_orders_builds_search_query(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    fake_shopify._payloads = [
        {
            "orders": {
                "edges": [
                    {
                        "cursor": "c1",
                        "node": {"id": "gid://shopify/Order/1", "name": "#1001"},
                    }
                ],
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            }
        }
    ]
    out = _call(
        "shopify_live_list_orders",
        store_key="lubelife",
        since="2026-01-01",
        financial_status="paid",
        sku="ABC-1",
        limit=10,
    )
    assert out["store_key"] == "lubelife"
    assert len(out["items"]) == 1
    assert out["next_cursor"] == "c1"

    variables = fake_shopify.calls[0][2]
    assert variables is not None
    assert variables["first"] == 10  # noqa: PLR2004
    qs = variables["query"]
    assert "updated_at:>=2026-01-01" in qs
    assert "financial_status:paid" in qs
    assert 'sku:"ABC-1"' in qs


def test_live_list_orders_no_filters_omits_query(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    fake_shopify._payloads = [
        {"orders": {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    ]
    out = _call("shopify_live_list_orders", store_key="lubelife")
    assert out["items"] == []
    assert out["next_cursor"] is None
    assert fake_shopify.calls[0][2]["query"] is None


def test_live_list_orders_next_cursor_only_when_has_next_page(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    # hasNextPage=False → next_cursor None even if endCursor is set.
    fake_shopify._payloads = [
        {
            "orders": {
                "edges": [{"cursor": "x", "node": {"id": "gid://shopify/Order/1"}}],
                "pageInfo": {"hasNextPage": False, "endCursor": "x"},
            }
        }
    ]
    out = _call("shopify_live_list_orders", store_key="lubelife")
    assert out["next_cursor"] is None


# ---------------------------------------------------------------------------
# Shopify live tools — products + inventory
# ---------------------------------------------------------------------------


def test_live_get_product_returns_node(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    fake_shopify._payloads = [{"product": {"id": "gid://shopify/Product/1", "title": "Widget"}}]
    out = _call(
        "shopify_live_get_product",
        store_key="lubelife",
        product_gid="gid://shopify/Product/1",
    )
    assert out["product"]["title"] == "Widget"


def test_live_list_products_builds_filter(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    fake_shopify._payloads = [
        {"products": {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    ]
    _call(
        "shopify_live_list_products",
        store_key="lubelife",
        status="active",
        title_query="widget",
        vendor="Acme",
    )
    qs = fake_shopify.calls[0][2]["query"]
    assert "status:active" in qs
    assert "title:*widget*" in qs
    assert 'vendor:"Acme"' in qs


def test_live_inventory_by_sku_uses_sku_query(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    fake_shopify._payloads = [
        {
            "productVariants": {
                "edges": [
                    {
                        "node": {
                            "id": "gid://shopify/ProductVariant/1",
                            "sku": "ABC-1",
                            "inventoryItem": {"id": "gid://shopify/InventoryItem/1"},
                        }
                    }
                ]
            }
        }
    ]
    out = _call("shopify_live_inventory_by_sku", store_key="lubelife", sku="ABC-1")
    assert out["sku"] == "ABC-1"
    assert len(out["variants"]) == 1
    assert fake_shopify.calls[0][2]["query"] == 'sku:"ABC-1"'


def test_live_inventory_rejects_blank_sku(live_container: Container) -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011 — FastMCP wraps ValueError
        _call("shopify_live_inventory_by_sku", store_key="lubelife", sku="")


# ---------------------------------------------------------------------------
# Shopify live tools — generic GraphQL
# ---------------------------------------------------------------------------


def test_live_graphql_passes_through(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    fake_shopify._payloads = [{"shop": {"name": "lubelife"}}]
    out = _call(
        "shopify_live_graphql",
        store_key="lubelife",
        query="{ shop { name } }",
        variables=None,
    )
    assert out["data"] == {"shop": {"name": "lubelife"}}


def test_live_graphql_translates_shopify_errors(
    live_container: Container, fake_shopify: FakeShopifyClient
) -> None:
    fake_shopify.set_error(ShopifyError("boom"))
    with pytest.raises(Exception):  # noqa: B017, PT011
        _call(
            "shopify_live_graphql",
            store_key="lubelife",
            query="{ shop { name } }",
        )


# ---------------------------------------------------------------------------
# Unknown store_key
# ---------------------------------------------------------------------------


def test_unknown_store_key_raises(live_container: Container) -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011
        _call(
            "shopify_live_get_order",
            store_key="not-a-store",
            order_gid="gid://shopify/Order/1",
        )


# ---------------------------------------------------------------------------
# OrderGroove live tools
# ---------------------------------------------------------------------------


def test_og_list_uses_api_key_and_returns_records(
    live_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_list_page(
        self: Any, *, page_size: int = 100, start_url: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        captured["page_size"] = page_size
        captured["start_url"] = start_url
        captured["api_key"] = self._api_key
        return (
            [{"public_id": "OG1", "live": True, "cancelled": None}],
            "https://restapi.ordergroove.com/subscriptions/?cursor=next",
        )

    monkeypatch.setattr(
        "mcp_server.tools.live_ordergroove.OrderGrooveClient.list_subscriptions_page",
        fake_list_page,
    )

    out = _call("ordergroove_live_list_subscriptions", store_key="lubelife", limit=50)
    assert captured["api_key"] == "og-test-key"
    assert captured["page_size"] == 50  # noqa: PLR2004
    assert captured["start_url"] is None
    assert out["items"][0]["public_id"] == "OG1"
    assert out["next_cursor"] == "https://restapi.ordergroove.com/subscriptions/?cursor=next"


def test_og_list_passes_cursor_through(
    live_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_list_page(
        self: Any, *, page_size: int = 100, start_url: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        captured["start_url"] = start_url
        return [], None

    monkeypatch.setattr(
        "mcp_server.tools.live_ordergroove.OrderGrooveClient.list_subscriptions_page",
        fake_list_page,
    )
    _call(
        "ordergroove_live_list_subscriptions",
        store_key="lubelife",
        cursor="https://og/next-page",
    )
    assert captured["start_url"] == "https://og/next-page"


def test_og_get_returns_record(live_container: Container, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(self: Any, public_id: str) -> dict[str, Any] | None:
        assert public_id == "OG1"
        return {"public_id": "OG1", "live": True}

    monkeypatch.setattr(
        "mcp_server.tools.live_ordergroove.OrderGrooveClient.get_subscription",
        fake_get,
    )
    out = _call("ordergroove_live_get_subscription", store_key="lubelife", public_id="OG1")
    assert out["subscription"]["public_id"] == "OG1"


def test_og_get_returns_null_on_404(
    live_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "mcp_server.tools.live_ordergroove.OrderGrooveClient.get_subscription",
        lambda self, public_id: None,  # noqa: ARG005
    )
    out = _call("ordergroove_live_get_subscription", store_key="lubelife", public_id="missing")
    assert out["subscription"] is None


def test_og_missing_api_key_raises(live_container: Container) -> None:
    # shopjo store has no ordergroove_api_key configured.
    with pytest.raises(Exception):  # noqa: B017, PT011
        _call("ordergroove_live_list_subscriptions", store_key="shopjo")


def test_og_unknown_store_raises(live_container: Container) -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011
        _call("ordergroove_live_list_subscriptions", store_key="nope")
