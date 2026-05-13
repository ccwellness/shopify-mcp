"""Unit tests for `build_provider` — the per-store subscription dispatcher."""

from __future__ import annotations

import pytest

from app.domain.enums import SubscriptionProvider as SubscriptionProviderEnum
from app.domain.models import CustomerId, StoreId
from app.services.subscriptions.base import UnknownProviderError, build_provider
from app.shopify.config import StoreConfig

STORE = StoreId(7)


def _cfg(
    *,
    provider: SubscriptionProviderEnum,
    api_key: str | None = None,
) -> StoreConfig:
    return StoreConfig(
        store_key="test",
        shop_domain="test.myshopify.com",
        client_id="cid",
        client_secret="csecret",  # noqa: S106 — test fixture
        webhook_secret="wsecret",  # noqa: S106 — test fixture
        plus=False,
        subscription_provider=provider,
        read_only=True,
        ordergroove_api_key=api_key,
        ordergroove_public_id=None,
    )


def _lookup(_: str) -> CustomerId | None:
    return None


def test_build_provider_returns_ordergroove_when_configured() -> None:
    cfg = _cfg(provider=SubscriptionProviderEnum.ORDERGROOVE, api_key="og-key")
    p = build_provider(cfg, STORE, _lookup)
    assert p is not None
    # OG provider's iter_active uses the OG client; just verify the type via
    # the iter_active attribute (Protocol-compatible).
    assert hasattr(p, "iter_active")


def test_build_provider_returns_none_when_ordergroove_key_missing() -> None:
    cfg = _cfg(provider=SubscriptionProviderEnum.ORDERGROOVE, api_key=None)
    assert build_provider(cfg, STORE, _lookup) is None


def test_build_provider_returns_none_for_unknown_provider() -> None:
    cfg = _cfg(provider=SubscriptionProviderEnum.UNKNOWN)
    assert build_provider(cfg, STORE, _lookup) is None


def test_build_provider_raises_for_native_until_built() -> None:
    cfg = _cfg(provider=SubscriptionProviderEnum.NATIVE)
    with pytest.raises(UnknownProviderError, match="NativeProvider not built"):
        build_provider(cfg, STORE, _lookup)
