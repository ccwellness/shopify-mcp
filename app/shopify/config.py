"""Per-store Shopify config — env-driven, dynamically discovered.

One `StoreConfig` per Shopify storefront. The roster is discovered at boot from
the environment: every `SHOPIFY_<KEY>_SHOP` variable defines a store whose
canonical `store_key` is `<KEY>` lowercased. This supports an unlimited number
of stores by config alone — no code change to add one.

A store authenticates one of two ways (token preferred):

- `SHOPIFY_<KEY>_ACCESS_TOKEN` — an Admin API access token (`shpat_…`). Used
  directly as `X-Shopify-Access-Token`; no OAuth exchange.
- `SHOPIFY_<KEY>_CLIENT_ID` + `SHOPIFY_<KEY>_CLIENT_SECRET` — OAuth
  client-credentials, exchanged for a 24h token by `TokenCache`.

Stores that satisfy neither auth path (or have no shop domain) are silently
skipped, so partial/dev setups don't crash.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.enums import SubscriptionProvider

# The storefronts identified during Phase 0 discovery. Retained for reference
# and as sensible defaults in docs/examples; discovery no longer depends on it.
KNOWN_STORE_KEYS: tuple[str, ...] = ("lubelife", "shopjo", "shopshibari")

# A store is anchored on its `_SHOP` variable. The capture group is the
# canonical key (uppercased in env, lowercased as `store_key`).
_STORE_KEY_RE = re.compile(r"^SHOPIFY_(?P<key>[A-Z0-9_]+)_SHOP$")


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
    # Admin API access token (`shpat_…`). When present it is used directly and
    # the OAuth client-credentials exchange is skipped entirely.
    access_token: str | None = None
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


def _discover_store_keys(env: Mapping[str, str]) -> list[str]:
    """Return sorted canonical store keys discovered from `SHOPIFY_<KEY>_SHOP`."""
    keys: set[str] = set()
    for name in env:
        match = _STORE_KEY_RE.match(name)
        if match and env.get(name):
            keys.add(match.group("key").lower())
    return sorted(keys)


def load_store_configs(
    env: Mapping[str, str] | None = None,
) -> dict[str, StoreConfig]:
    """Return a dict of store_key → StoreConfig for every store with real creds.

    `env` defaults to `os.environ`; callers can pass a different mapping for
    tests. A store is included iff it has a shop domain AND a usable auth path
    (a real access token, or a real client_id + client_secret). Stores that
    satisfy neither are silently skipped — intentional for partial setups.
    """
    env = env if env is not None else os.environ
    out: dict[str, StoreConfig] = {}
    for key in _discover_store_keys(env):
        upper = key.upper()
        shop_domain = env.get(f"SHOPIFY_{upper}_SHOP", "")
        if not shop_domain:
            continue

        access_token = env.get(f"SHOPIFY_{upper}_ACCESS_TOKEN") or None
        client_id = env.get(f"SHOPIFY_{upper}_CLIENT_ID", "")
        client_secret = env.get(f"SHOPIFY_{upper}_CLIENT_SECRET", "")

        has_token = not _placeholder(access_token)
        has_oauth = not _placeholder(client_id) and not _placeholder(client_secret)
        if not has_token and not has_oauth:
            continue

        # webhook_secret defaults to client_secret per .env.example commentary —
        # legacy Shopify behavior; verified empirically on first signed delivery.
        # May be empty in token-only setups (webhooks aren't used by live MCP).
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
            access_token=access_token if has_token else None,
            ordergroove_api_key=og_key if og_key else None,
            ordergroove_public_id=og_public_id if og_public_id else None,
        )
    return out
