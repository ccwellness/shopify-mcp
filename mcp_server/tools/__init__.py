"""MCP tool implementations — one module per concern.

Importing this package registers every tool against `mcp_server.server.mcp`
via the `@mcp.tool` decorators. The entrypoint (`mcp_server.__main__`)
just imports this package and then calls `mcp.run_*`.
"""

from __future__ import annotations

from mcp_server.tools import (
    analytics,
    compare,
    inventory,
    live_ordergroove,
    live_shopify,
    orders,
    products,
    stores,
    subscriptions,
)

__all__ = [
    "analytics",
    "compare",
    "inventory",
    "live_ordergroove",
    "live_shopify",
    "orders",
    "products",
    "stores",
    "subscriptions",
]
