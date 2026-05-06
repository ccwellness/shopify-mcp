"""Shopify client exception hierarchy.

Callers catch `ShopifyError` to handle anything the client raises. More
specific exceptions exist for the cases where the caller's response
differs (e.g. retry vs surface to user).
"""

from __future__ import annotations

from typing import Any


class ShopifyError(Exception):
    """Base class — every other Shopify client exception inherits from this."""


class AuthError(ShopifyError):
    """OAuth token exchange failed (4xx from /admin/oauth/access_token)."""


class ThrottledError(ShopifyError):
    """The query was throttled and exceeded the client's retry budget."""


class ReadOnlyViolation(ShopifyError):
    """A mutation was attempted against a store flagged read_only=True (TR-46)."""


class ShopifyGraphQLError(ShopifyError):
    """The response had top-level `errors` that aren't THROTTLED."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__(self._summarize(errors))
        self.errors = errors

    _SUMMARY_LIMIT = 3

    @staticmethod
    def _summarize(errors: list[dict[str, Any]]) -> str:
        msgs = []
        limit = ShopifyGraphQLError._SUMMARY_LIMIT
        for e in errors[:limit]:
            msg = e.get("message", "<no message>")
            code = e.get("extensions", {}).get("code")
            msgs.append(f"{code}: {msg}" if code else msg)
        suffix = f" (+{len(errors) - limit} more)" if len(errors) > limit else ""
        return "; ".join(msgs) + suffix
