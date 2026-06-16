"""Live OrderGroove MCP tools — bypass the local database.

These tools call the OrderGroove REST API directly. Useful for the two
stores currently on OG (lubelife, shopjo) where the local
`subscription_contracts` table is only as fresh as the last sync.

A new `OrderGrooveClient` is constructed per call. The client is cheap
(no connection pool state) and each store has its own API key, so
caching one per process would just complicate teardown.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.integrations.ordergroove.client import (
    OrderGrooveClient,
    OrderGrooveError,
)
from app.shopify.config import StoreConfig
from mcp_server.audit import audited
from mcp_server.server import mcp, services

_MAX_LIMIT = 100


class LiveSubscriptionOut(BaseModel):
    """Raw OrderGroove subscription record, or null if not found."""

    store_key: str
    subscription: dict[str, Any] | None


class LiveSubscriptionListOut(BaseModel):
    """One page of raw OG subscription records plus the opaque next_cursor.

    `next_cursor` is the full `next` URL OG returns — pass it back as
    `cursor=` on the next call. Treat it as opaque; do not hand-craft.
    """

    store_key: str
    items: list[dict[str, Any]]
    next_cursor: str | None


def _client_for(store_key: str) -> OrderGrooveClient:
    configs = services().store_configs
    cfg: StoreConfig | None = configs.get(store_key)
    if cfg is None:
        raise ValueError(f"unknown store_key: {store_key!r}")
    if not cfg.ordergroove_api_key:
        raise ValueError(
            f"store {store_key!r} has no OrderGroove API key configured "
            f"(set ORDERGROOVE_{store_key.upper()}_API_KEY)"
        )
    return OrderGrooveClient(cfg.ordergroove_api_key)


@mcp.tool
@audited("ordergroove_live_list_subscriptions")
def ordergroove_live_list_subscriptions(
    store_key: str = Field(description="Store key, e.g. 'lubelife' or 'shopjo'."),
    limit: int = Field(
        default=100,
        ge=1,
        le=_MAX_LIMIT,
        description="Page size passed to OrderGroove (max 100 in practice).",
    ),
    cursor: str | None = Field(
        default=None,
        description="Opaque next_cursor URL from a prior call. Omit to start from page 1.",
    ),
) -> LiveSubscriptionListOut:
    """Page through subscriptions LIVE from OrderGroove.

    Returns raw OG records — see `project_ordergroove_api.md` memory for
    the response shape (public_id, external_id, customer, every,
    every_period, live, cancelled, etc.). For local-DB-backed queries
    that join customer info across stores, use `list_subscriptions`.
    """
    client = _client_for(store_key)
    try:
        results, next_url = client.list_subscriptions_page(
            page_size=limit, start_url=cursor or None
        )
    except OrderGrooveError as exc:
        raise ValueError(str(exc)) from exc
    return LiveSubscriptionListOut(
        store_key=store_key,
        items=results,
        next_cursor=next_url,
    )


@mcp.tool
@audited("ordergroove_live_get_subscription")
def ordergroove_live_get_subscription(
    store_key: str = Field(description="Store key, e.g. 'lubelife' or 'shopjo'."),
    public_id: str = Field(description="OG public_id (the canonical OG identifier)."),
) -> LiveSubscriptionOut:
    """Fetch one OrderGroove subscription LIVE by public_id.

    `subscription` is null if OG returned 404. Use this when you have an
    OG public_id and want the freshest state (e.g. just after a
    cancellation through the customer portal).
    """
    client = _client_for(store_key)
    try:
        record = client.get_subscription(public_id)
    except OrderGrooveError as exc:
        raise ValueError(str(exc)) from exc
    return LiveSubscriptionOut(store_key=store_key, subscription=record)
