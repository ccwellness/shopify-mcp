"""Bearer-token middleware for the MCP HTTP transport (TR-5).

Stdio transport runs without auth (local OS user authorization). The HTTP
transport sits behind the same token store as the REST API: the
`Authorization: Bearer <token>` header is validated through `AuthService`,
and the token name is stashed in the `mcp_server.audit` contextvar so
every audit row records the right caller identity.

FastMCP's HTTP server returns an ASGI app (via `mcp.http_app()`); we wrap
it in a tiny ASGI middleware that runs before the MCP handler.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .audit import reset_caller_identity, set_caller_identity
from .server import services

_log = logging.getLogger(__name__)

_ASGI_HTTP = "http"


class BearerAuthMiddleware:
    """Minimal ASGI middleware enforcing a bearer token on every request."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != _ASGI_HTTP:
            await self._app(scope, receive, send)
            return

        token_name = await _authorize(scope, send)
        if token_name is None:
            return  # _send_401 already responded

        ctx_token = set_caller_identity(token_name)
        try:
            await self._app(scope, receive, send)
        finally:
            reset_caller_identity(ctx_token)


async def _authorize(
    scope: dict[str, Any],
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> str | None:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    raw = headers.get("authorization", "")
    if not raw.startswith("Bearer "):
        await _send_401(send, "missing bearer token")
        return None
    plaintext = raw.removeprefix("Bearer ").strip()

    token = services().auth.validate(plaintext)
    if token is None:
        await _send_401(send, "invalid or expired token")
        return None
    return token.name


async def _send_401(send: Callable[[dict[str, Any]], Awaitable[None]], message: str) -> None:
    body = f'{{"error":"{message}"}}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
