"""OrderGroove REST API client.

Wraps `https://restapi.ordergroove.com/` behind an httpx-based client that
adds the `x-api-key` header and walks Django-REST cursor pagination via
the response's `next` URL.

Per-store credentials live on `StoreConfig.ordergroove_api_key`. The
`OrderGrooveProvider` (in `app.services.subscriptions.ordergroove`)
constructs the client lazily and is the only allowed caller.
"""

from __future__ import annotations

from app.integrations.ordergroove.client import (
    OrderGrooveAuthError,
    OrderGrooveClient,
    OrderGrooveError,
)

__all__ = ["OrderGrooveAuthError", "OrderGrooveClient", "OrderGrooveError"]
