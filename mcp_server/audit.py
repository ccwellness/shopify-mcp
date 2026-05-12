"""`@audited` decorator — records one `api_audit_log` row per tool call (TR-6).

The decorator wraps a FastMCP tool function. It:

  1. Captures the call's start time and sanitized params.
  2. Invokes the underlying tool.
  3. Writes an audit row with `surface=mcp`, status_code 200 on success
     or 500 on exception, and the latency in ms.
  4. Re-raises on failure so the MCP runtime returns a tool error.

`caller_identity` defaults to `'local'` for stdio sessions. The HTTP
transport's auth middleware (mcp_server.auth) stores the token name in
a contextvar so HTTP-driven calls record the right caller.
"""

from __future__ import annotations

import contextvars
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any, TypeVar

from app.domain.enums import ApiSurface

from .server import services

_log = logging.getLogger(__name__)

# Set by the HTTP-transport auth middleware before each request, cleared
# after. Stdio sessions leave this as the default 'local'.
_caller_identity: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_mcp_caller_identity", default="local"
)


def set_caller_identity(name: str) -> contextvars.Token[str]:
    """Push a new caller identity onto the contextvar. Returns the reset token."""
    return _caller_identity.set(name)


def reset_caller_identity(token: contextvars.Token[str]) -> None:
    _caller_identity.reset(token)


F = TypeVar("F", bound=Callable[..., Any])


def audited(tool_name: str) -> Callable[[F], F]:
    """Wrap a tool function with one-row audit logging."""

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> object:
            start = time.monotonic()
            params = _params_dict(args, kwargs)
            status = 200
            try:
                return fn(*args, **kwargs)
            except Exception:
                status = 500
                raise
            finally:
                latency_ms = int((time.monotonic() - start) * 1000)
                try:
                    services().audit.record(
                        caller_identity=_caller_identity.get(),
                        store_id=None,  # MCP tools are cross-store unless a tool args includes it
                        surface=ApiSurface.MCP.value,
                        route_or_tool=tool_name,
                        params=params,
                        status_code=status,
                        latency_ms=latency_ms,
                        request_id=None,
                        ts=datetime.now(tz=UTC),
                    )
                except Exception:  # noqa: BLE001 — audit must not mask tool errors
                    _log.exception("mcp audit write failed for tool=%s", tool_name)

        return wrapper  # type: ignore[return-value]

    return decorator


def _params_dict(args: tuple[object, ...], kwargs: dict[str, object]) -> dict[str, object]:
    # FastMCP tools usually receive kwargs only (Pydantic-bound), so args
    # is empty. Keep both for safety. The AuditService sanitizer strips PII.
    out: dict[str, object] = {}
    if args:
        out["_positional"] = [_jsonable(a) for a in args]
    for k, v in kwargs.items():
        out[k] = _jsonable(v)
    return out


def _jsonable(value: object) -> object:
    """Coerce common non-JSON types so the audit JSON column doesn't error.

    Pydantic models, datetimes, decimals, sets — drop to a representation
    the JSON sanitizer in AuditService can serialize.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set | frozenset):
        return sorted(value)  # type: ignore[type-var]
    return value
