"""`list_stores` MCP tool — active stores the connector knows about."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_server.audit import audited
from mcp_server.server import mcp, services


class StoreOut(BaseModel):
    id: int
    store_key: str = Field(description="Stable lowercase handle (e.g. 'lubelife').")
    shop_domain: str
    display_name: str
    plus: bool
    currency_code: str | None


class ListStoresOut(BaseModel):
    items: list[StoreOut]


@mcp.tool
@audited("list_stores")
def list_stores() -> ListStoresOut:
    """List every active store the connector is syncing.

    Use this when the user asks "which stores do we cover?" or before
    calling another tool with a specific store_id you don't already know.
    """
    rows = services().stores.list_active()
    return ListStoresOut(
        items=[
            StoreOut(
                id=int(s.id),
                store_key=s.store_key,
                shop_domain=s.shop_domain,
                display_name=s.display_name,
                plus=s.plus,
                currency_code=s.currency_code,
            )
            for s in rows
        ]
    )
