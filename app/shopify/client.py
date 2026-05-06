"""ShopifyClient — sync GraphQL Admin API client (TR-7, TR-8, TR-9, TR-46).

One client instance handles all stores; per-store state (tokens, throttle
counters) is keyed by store_key.

Read-only enforcement: by default every store is `read_only=True`. The
client refuses to send anything that parses as a `mutation { ... }` against
a read_only store, regardless of scope grants.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from prometheus_client import Counter

from app.shopify.auth import TokenCache
from app.shopify.config import StoreConfig
from app.shopify.errors import (
    AuthError,
    ReadOnlyViolation,
    ShopifyError,
    ShopifyGraphQLError,
    ThrottledError,
)
from app.shopify.throttle import parse_throttle_status, sleep_if_low

DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 30.0
_HTTP_UNAUTHORIZED = 401
_HTTP_CLIENT_ERROR_FLOOR = 400

# Strip line / block comments + leading whitespace so we don't get fooled by
# `# mutation` comments. (We're not parsing GraphQL — this is a sanity gate
# layered on top of the real protection, which is the scope set on the app.)
_COMMENT_RE = re.compile(r"(#[^\n]*\n)|(\s+)", re.MULTILINE)


def _is_mutation(query: str) -> bool:
    stripped = _COMMENT_RE.sub("", query)
    return stripped.lstrip().lower().startswith("mutation")


# ---------------------------------------------------------------------------
# Prometheus metrics (TR-9)
# ---------------------------------------------------------------------------

_QUERY_COST_POINTS = Counter(
    "shopify_query_cost_points_total",
    "Sum of actualQueryCost across all GraphQL responses, per store.",
    ["store"],
)
_QUERY_RETRIES = Counter(
    "shopify_query_retries_total",
    "Number of throttle-triggered retries, per store.",
    ["store"],
)
_QUERY_ERRORS = Counter(
    "shopify_query_errors_total",
    "Number of failed queries, per store and error class.",
    ["store", "kind"],
)


class ShopifyClient:
    """Sync GraphQL client. One instance, all stores."""

    def __init__(
        self,
        configs: dict[str, StoreConfig],
        *,
        http: httpx.Client | None = None,
        token_cache: TokenCache | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._configs = configs
        self._http = http or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, http2=True)
        self._tokens = token_cache or TokenCache(http=self._http)
        self._max_retries = max_retries

    def store(self, store_key: str) -> StoreConfig:
        cfg = self._configs.get(store_key)
        if cfg is None:
            raise ShopifyError(f"Unknown store_key: {store_key!r}")
        return cfg

    def query(
        self,
        store_key: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_mutation: bool = False,
    ) -> dict[str, Any]:
        """Execute a GraphQL query against `store_key`. Returns parsed `data`.

        `allow_mutation=True` bypasses the TR-46 read-only mutation block.
        Use it ONLY for mutations that don't change shop data — most notably
        `bulkOperationRunQuery`, which is a mutation in GraphQL terms (it
        enqueues a server-side bulk job) but doesn't write any shop state.
        Every caller passing this flag should be code-reviewed.
        """
        cfg = self.store(store_key)
        if _is_mutation(query) and cfg.read_only and not allow_mutation:
            raise ReadOnlyViolation(
                f"Mutation blocked: store {cfg.store_key!r} is read_only=True (TR-46)."
            )

        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            token = self._tokens.get(cfg)
            try:
                resp = self._http.post(
                    cfg.graphql_url,
                    json=payload,
                    headers={
                        "X-Shopify-Access-Token": token,
                        "Content-Type": "application/json",
                    },
                )
            except httpx.HTTPError as exc:
                _QUERY_ERRORS.labels(store=store_key, kind="transport").inc()
                last_error = ShopifyError(f"HTTP transport error: {exc}")
                # Brief backoff before retry on transport errors
                sleep_if_low({}, attempt=attempt + 1)
                continue

            if resp.status_code == _HTTP_UNAUTHORIZED:
                # Token may have been revoked or expired early — invalidate + retry once.
                _QUERY_ERRORS.labels(store=store_key, kind="auth").inc()
                self._tokens.invalidate(store_key)
                if attempt < self._max_retries:
                    continue
                raise AuthError(f"401 from {cfg.shop_domain} after token refresh")

            if resp.status_code >= _HTTP_CLIENT_ERROR_FLOOR:
                _QUERY_ERRORS.labels(store=store_key, kind=f"http_{resp.status_code}").inc()
                raise ShopifyError(f"HTTP {resp.status_code} from {cfg.shop_domain}")

            body: dict[str, Any] = resp.json()
            extensions = body.get("extensions") or {}
            parsed_cost = parse_throttle_status(extensions)
            _QUERY_COST_POINTS.labels(store=store_key).inc(parsed_cost["actual_cost"])

            errors = body.get("errors") or []
            throttled = any(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors)
            if throttled:
                _QUERY_RETRIES.labels(store=store_key).inc()
                if attempt < self._max_retries:
                    sleep_if_low(extensions, attempt=attempt + 1)
                    continue
                raise ThrottledError(
                    f"THROTTLED after {self._max_retries} retries on {cfg.store_key}"
                )

            if errors:
                _QUERY_ERRORS.labels(store=store_key, kind="graphql").inc()
                raise ShopifyGraphQLError(errors)

            # Success — but if the bucket is low, sleep so the *next* caller
            # doesn't immediately throttle. Doesn't affect this call's latency
            # budget meaningfully (typically <500ms unless the bucket is empty).
            sleep_if_low(extensions, attempt=0)

            return body.get("data") or {}

        # Should be unreachable, but make the type-checker and SREs happy.
        raise last_error or ThrottledError("query loop exhausted without success")

    def close(self) -> None:
        self._tokens.close()
        self._http.close()
