"""Dynamic store discovery + token-preferred auth (no DB)."""

from __future__ import annotations

import httpx
import pytest

from app.config_mode import resolve_data_source
from app.domain.enums import SubscriptionProvider
from app.shopify.auth import TokenCache
from app.shopify.config import StoreConfig, load_store_configs


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "SHOPIFY_LUBELIFE_SHOP": "lubelife.myshopify.com",
        "SHOPIFY_LUBELIFE_ACCESS_TOKEN": "shpat_real_token",
    }
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovers_token_only_store() -> None:
    cfgs = load_store_configs(_base_env())
    assert set(cfgs) == {"lubelife"}
    assert cfgs["lubelife"].access_token == "shpat_real_token"
    assert cfgs["lubelife"].shop_domain == "lubelife.myshopify.com"


def test_discovers_oauth_only_store() -> None:
    env = {
        "SHOPIFY_SHOPJO_SHOP": "system-jo.myshopify.com",
        "SHOPIFY_SHOPJO_CLIENT_ID": "cid",
        "SHOPIFY_SHOPJO_CLIENT_SECRET": "csec",
    }
    cfgs = load_store_configs(env)
    assert set(cfgs) == {"shopjo"}
    assert cfgs["shopjo"].access_token is None
    assert cfgs["shopjo"].client_id == "cid"


def test_discovers_many_stores_unbounded() -> None:
    env: dict[str, str] = {}
    for key in ("alpha", "beta", "gamma", "delta"):
        env[f"SHOPIFY_{key.upper()}_SHOP"] = f"{key}.myshopify.com"
        env[f"SHOPIFY_{key.upper()}_ACCESS_TOKEN"] = f"shpat_{key}"
    cfgs = load_store_configs(env)
    assert set(cfgs) == {"alpha", "beta", "gamma", "delta"}


def test_skips_store_without_auth() -> None:
    env = {"SHOPIFY_NOAUTH_SHOP": "noauth.myshopify.com"}
    assert load_store_configs(env) == {}


def test_skips_store_without_shop_domain() -> None:
    env = {"SHOPIFY_GHOST_ACCESS_TOKEN": "shpat_x"}  # no _SHOP anchor
    assert load_store_configs(env) == {}


def test_skips_placeholder_credentials() -> None:
    env = {
        "SHOPIFY_LUBELIFE_SHOP": "lubelife.myshopify.com",
        "SHOPIFY_LUBELIFE_CLIENT_ID": "replace-with-client-id",
        "SHOPIFY_LUBELIFE_CLIENT_SECRET": "replace-with-client-secret",
    }
    assert load_store_configs(env) == {}


def test_token_preferred_over_oauth() -> None:
    env = _base_env(
        SHOPIFY_LUBELIFE_CLIENT_ID="cid",
        SHOPIFY_LUBELIFE_CLIENT_SECRET="csec",
    )
    cfg = load_store_configs(env)["lubelife"]
    assert cfg.access_token == "shpat_real_token"
    assert cfg.client_id == "cid"  # retained, but token wins at auth time


def test_ordergroove_keys_wired() -> None:
    env = _base_env(
        SHOPIFY_LUBELIFE_SUBSCRIPTION_PROVIDER="ordergroove",
        ORDERGROOVE_LUBELIFE_API_KEY="og-key",
        ORDERGROOVE_LUBELIFE_PUBLIC_ID="og-pub",
    )
    cfg = load_store_configs(env)["lubelife"]
    assert cfg.subscription_provider is SubscriptionProvider.ORDERGROOVE
    assert cfg.ordergroove_api_key == "og-key"
    assert cfg.ordergroove_public_id == "og-pub"


# ---------------------------------------------------------------------------
# Token-preferred auth
# ---------------------------------------------------------------------------


def _cfg(*, access_token: str | None) -> StoreConfig:
    return StoreConfig(
        store_key="lubelife",
        shop_domain="lubelife.myshopify.com",
        client_id="cid",
        client_secret="csec",  # noqa: S106 — test placeholder
        webhook_secret="wsec",  # noqa: S106 — test placeholder
        plus=False,
        subscription_provider=SubscriptionProvider.UNKNOWN,
        read_only=True,
        access_token=access_token,
    )


def test_token_cache_returns_static_token_without_exchange() -> None:
    def _boom(*_args: object, **_kwargs: object) -> httpx.Response:
        raise AssertionError("OAuth exchange must not run when a token is set")

    http = httpx.Client(transport=httpx.MockTransport(_boom))
    cache = TokenCache(http=http)
    assert cache.get(_cfg(access_token="shpat_static")) == "shpat_static"
    http.close()


def test_token_cache_falls_back_to_oauth_when_no_token() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "exchanged", "expires_in": 86400})

    http = httpx.Client(transport=httpx.MockTransport(_handler))
    cache = TokenCache(http=http)
    assert cache.get(_cfg(access_token=None)) == "exchanged"
    http.close()


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"DATABASE_URL": "postgresql://x"}, "db"),
        ({}, "live"),
        ({"DATABASE_URL": "postgresql://x", "MCP_DATA_SOURCE": "live"}, "live"),
        ({"MCP_DATA_SOURCE": "db"}, "db"),
        ({"MCP_DATA_SOURCE": "garbage", "DATABASE_URL": "postgresql://x"}, "db"),
    ],
)
def test_resolve_data_source(env: dict[str, str], expected: str) -> None:
    assert resolve_data_source(env) == expected
