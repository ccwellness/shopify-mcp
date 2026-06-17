"""Shopify OAuth token cache.

Custom Apps created via the Dev Dashboard use the OAuth client_credentials
grant: POST client_id + client_secret → 24h access token. The connector
caches tokens in memory and refreshes on demand.

TR-1 — neither credentials nor tokens are ever logged. The token cache
exposes only the access token string at call sites; everything else stays
private.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from app.shopify.config import StoreConfig
from app.shopify.errors import AuthError

_REFRESH_SAFETY_MARGIN = timedelta(minutes=5)
"""Refresh tokens at least this long before they expire so an in-flight
request never gets caught with a stale token."""

_HTTP_OK = 200


@dataclass(frozen=True, slots=True)
class _CachedToken:
    access_token: str
    expires_at: datetime


class TokenCache:
    """Thread-safe in-memory token cache, one entry per store_key."""

    def __init__(self, http: httpx.Client | None = None) -> None:
        # Caller can inject a configured client (timeouts, transport mocks, etc.).
        self._http = http or httpx.Client(timeout=30.0)
        self._tokens: dict[str, _CachedToken] = {}
        self._lock = threading.Lock()

    def get(self, store: StoreConfig) -> str:
        """Return a valid access token for `store`, refreshing if needed.

        A configured Admin API access token wins: it's returned directly and
        no OAuth exchange happens. Stores without a token fall back to the
        client-credentials grant.
        """
        if store.access_token:
            return store.access_token
        with self._lock:
            cached = self._tokens.get(store.store_key)
            if cached is not None and cached.expires_at - _REFRESH_SAFETY_MARGIN > datetime.now(
                tz=UTC
            ):
                return cached.access_token
            fresh = self._exchange(store)
            self._tokens[store.store_key] = fresh
            return fresh.access_token

    def invalidate(self, store_key: str) -> None:
        """Drop any cached token for `store_key` — used on 401 responses."""
        with self._lock:
            self._tokens.pop(store_key, None)

    def _exchange(self, store: StoreConfig) -> _CachedToken:
        try:
            resp = self._http.post(
                store.oauth_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": store.client_id,
                    "client_secret": store.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise AuthError(f"OAuth exchange failed for {store.store_key}: {exc}") from exc

        if resp.status_code != _HTTP_OK:
            # Don't echo the response body — could echo back creds in some failure modes.
            raise AuthError(
                f"OAuth exchange returned HTTP {resp.status_code} for {store.store_key}"
            )
        body = resp.json()
        access_token = body.get("access_token")
        if not access_token:
            raise AuthError(f"OAuth response missing access_token for {store.store_key}")
        expires_in = int(body.get("expires_in") or 86400)
        return _CachedToken(
            access_token=access_token,
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=expires_in),
        )

    def close(self) -> None:
        self._http.close()
