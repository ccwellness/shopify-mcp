"""Per-store Shopify config — env-driven.

One `StoreConfig` per Shopify storefront. Loaded once at boot via
`load_store_configs(env)`. Stores with placeholder credentials are skipped
(see `_HAS_REAL_CREDS`) so dev environments missing one store's config
don't crash — Phase 0 leaves shopshibari deferred this way.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.enums import SubscriptionProvider

# The three storefronts the connector targets. Order is informational only.
KNOWN_STORE_KEYS: tuple[str, ...] = ("lubelife", "shopjo", "shopshibari")


@dataclass(frozen=True, slots=True, kw_only=True)
class StoreConfig:
    """Everything the Shopify client needs to authenticate + transact for one store."""

    store_key: str
    shop_domain: str
    client_id: str
    client_secret: str
    webhook_secret: str
    plus: bool
    subscription_provider: SubscriptionProvider
    read_only: bool
    # OrderGroove integration credentials — populated only for stores whose
    # `subscription_provider == ORDERGROOVE`. Stays None on stores where the
    # key hasn't been added to .env yet (the provider dispatcher in
    # SyncService treats that as 'subscriptions sync disabled for now').
    ordergroove_api_key: str | None = None
    ordergroove_public_id: str | None = None

    @property
    def graphql_url(self) -> str:
        # Pinned API version (TR-7). Kept in sync with .env's SHOPIFY_API_VERSION.
        api_version = os.environ.get("SHOPIFY_API_VERSION", "2026-04")
        return f"https://{self.shop_domain}/admin/api/{api_version}/graphql.json"

    @property
    def oauth_token_url(self) -> str:
        return f"https://{self.shop_domain}/admin/oauth/access_token"


def _placeholder(value: str | None) -> bool:
    """Return True if a credential value is missing or still the .env.example default."""
    if not value:
        return True
    return value.startswith("replace-with-")


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _provider(value: str | None) -> SubscriptionProvider:
    if not value:
        return SubscriptionProvider.UNKNOWN
    try:
        return SubscriptionProvider(value.strip().lower())
    except ValueError:
        return SubscriptionProvider.UNKNOWN


def load_store_configs(
    env: Mapping[str, str] | None = None,
) -> dict[str, StoreConfig]:
    """Return a dict of store_key → StoreConfig for every store with real creds.

    `env` defaults to `os.environ`; callers can pass a different mapping for
    tests. Stores whose `CLIENT_ID` or `CLIENT_SECRET` is still a placeholder
    are silently skipped — this is intentional for partial Phase 0 setups.
    """
    env = env if env is not None else os.environ
    out: dict[str, StoreConfig] = {}
    for key in KNOWN_STORE_KEYS:
        upper = key.upper()
        client_id = env.get(f"SHOPIFY_{upper}_CLIENT_ID", "")
        client_secret = env.get(f"SHOPIFY_{upper}_CLIENT_SECRET", "")
        if _placeholder(client_id) or _placeholder(client_secret):
            continue

        shop_domain = env.get(f"SHOPIFY_{upper}_SHOP", "")
        if not shop_domain:
            continue

        # webhook_secret defaults to client_secret per .env.example commentary —
        # legacy Shopify behavior; verified empirically on first signed delivery.
        webhook_secret = env.get(f"SHOPIFY_{upper}_WEBHOOK_SECRET") or client_secret

        og_key = env.get(f"ORDERGROOVE_{upper}_API_KEY") or None
        og_public_id = env.get(f"ORDERGROOVE_{upper}_PUBLIC_ID") or None

        out[key] = StoreConfig(
            store_key=key,
            shop_domain=shop_domain,
            client_id=client_id,
            client_secret=client_secret,
            webhook_secret=webhook_secret,
            plus=_bool(env.get(f"SHOPIFY_{upper}_PLUS"), default=False),
            subscription_provider=_provider(env.get(f"SHOPIFY_{upper}_SUBSCRIPTION_PROVIDER")),
            read_only=_bool(env.get(f"SHOPIFY_{upper}_READ_ONLY"), default=True),
            ordergroove_api_key=og_key if og_key else None,
            ordergroove_public_id=og_public_id if og_public_id else None,
        )
    return out
