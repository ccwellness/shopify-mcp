"""Shared helpers for live Shopify GraphQL repositories and tools.

GraphQL connections come back as `{edges: [{cursor, node}], pageInfo: {...}}`.
These helpers flatten that shape, walk every page of a connection, and parse
the numeric tail out of a Shopify GID — the operations every live repository
repeats.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any


def flatten_edges(connection: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str | None]:
    """Return `(nodes, end_cursor_or_None)` from a GraphQL connection.

    `end_cursor` is non-None only when `pageInfo.hasNextPage` is true, so a
    caller can treat None as "no more pages".
    """
    if not connection:
        return [], None
    edges = connection.get("edges") or []
    nodes = [e["node"] for e in edges if isinstance(e, dict) and e.get("node")]
    page_info = connection.get("pageInfo") or {}
    end_cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
    return nodes, end_cursor if isinstance(end_cursor, str) else None


def iter_all_nodes(
    run: Callable[[str | None], dict[str, Any] | None],
) -> Iterator[dict[str, Any]]:
    """Yield every node across all pages of a connection.

    `run(after)` issues one page given an opaque cursor (None for the first
    page) and returns the connection dict (`{edges, pageInfo}`). Iteration
    stops when a page reports no next cursor — uncapped but always terminating.
    """
    after: str | None = None
    while True:
        connection = run(after)
        nodes, next_cursor = flatten_edges(connection)
        yield from nodes
        if not next_cursor:
            return
        after = next_cursor


def gid_tail(gid: str | None) -> int | None:
    """Parse the numeric id out of a Shopify GID (`gid://shopify/Order/123` → 123)."""
    if not gid:
        return None
    tail = str(gid).rsplit("/", 1)[-1]
    # Drop any query suffix Shopify occasionally appends (e.g. ?inventory_item_id=).
    tail = tail.split("?", 1)[0]
    return int(tail) if tail.isdigit() else None
