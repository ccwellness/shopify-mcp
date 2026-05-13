"""HTTP client for OrderGroove's REST API.

Probe summary (2026-05-13, lubelife):
- Base URL: `https://restapi.ordergroove.com/`
- Auth:    single header `x-api-key: <value>` (lowercase).
- List:    `GET /subscriptions/` returns every subscription for the
           merchant the key belongs to; no `customer` filter required.
- Paging:  DRF-style envelope `{count, next, previous, results}` with
           `next` carrying the full URL to the next page.
- Volume:  ~2k subscriptions on lubelife → paging always matters.

This module exposes a thin client; normalization to domain
`SubscriptionContract` lives in `app.services.subscriptions.ordergroove`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://restapi.ordergroove.com"
_SUBSCRIPTIONS_PATH = "/subscriptions/"
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403


class OrderGrooveError(RuntimeError):
    """Any OrderGroove API call that didn't return a JSON body we understand."""


class OrderGrooveAuthError(OrderGrooveError):
    """The API key was rejected (401/403). Surface separately so the caller can
    pause the per-store sync without retrying the whole window."""


class OrderGrooveClient:
    """Per-store OrderGroove REST client.

    Construct one per store (each store has its own API key). The client is
    cheap — no connection pooling state — so building it inside a sync run
    is fine.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise OrderGrooveError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def iter_subscriptions(self, *, page_size: int = 100) -> Iterator[dict[str, Any]]:
        """Yield every subscription record, walking `next` pages until None.

        Each yielded value is the raw OrderGroove dict — normalization to
        `SubscriptionContract` is the provider's job.
        """
        url: str | None = f"{self._base_url}{_SUBSCRIPTIONS_PATH}?page_size={page_size}"
        headers = {"x-api-key": self._api_key, "Accept": "application/json"}
        with httpx.Client(timeout=self._timeout) as client:
            while url is not None:
                resp = client.get(url, headers=headers)
                _raise_for_status(resp)
                body = resp.json()
                if not isinstance(body, dict) or "results" not in body:
                    shape = sorted(body.keys()) if isinstance(body, dict) else type(body).__name__
                    raise OrderGrooveError(f"unexpected response shape from {url!r}: keys={shape}")
                for record in body["results"]:
                    if isinstance(record, dict):
                        yield record
                next_url = body.get("next")
                url = next_url if isinstance(next_url, str) and next_url else None


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
        raise OrderGrooveAuthError(
            f"OrderGroove auth failed: HTTP {resp.status_code} for {resp.url!r}"
        )
    if resp.status_code >= 400:  # noqa: PLR2004
        raise OrderGrooveError(
            f"OrderGroove HTTP {resp.status_code} for {resp.url!r}: {resp.text[:300]}"
        )
