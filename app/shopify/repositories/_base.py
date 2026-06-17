"""Shared base + GID helpers for live Shopify repositories.

Every live repository needs the same two things: the shared `ShopifyClient`
(to run GraphQL against a store) and the `StoreIndex` (to map between the
synthetic numeric `store_id` the MCP tools speak and the `store_key` the
client authenticates with). `_LiveRepo` bundles both.
"""

from __future__ import annotations

from app.domain.models import StoreId
from app.shopify.client import ShopifyClient
from app.shopify.repositories.store_index import StoreIndex


def order_gid(legacy_id: int) -> str:
    return f"gid://shopify/Order/{legacy_id}"


def product_gid(legacy_id: int) -> str:
    return f"gid://shopify/Product/{legacy_id}"


def variant_gid(legacy_id: int) -> str:
    return f"gid://shopify/ProductVariant/{legacy_id}"


def customer_gid(legacy_id: int) -> str:
    return f"gid://shopify/Customer/{legacy_id}"


class _LiveRepo:
    """Common deps for a live repository."""

    def __init__(self, client: ShopifyClient, index: StoreIndex) -> None:
        self._client = client
        self._index = index

    def _key(self, store_id: StoreId | int) -> str | None:
        return self._index.key_for(store_id)

    def _query(self, store_key: str, query: str, variables: dict | None = None) -> dict:
        return self._client.query(store_key, query, variables)
