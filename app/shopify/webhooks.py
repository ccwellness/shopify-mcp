"""HMAC verification + topic allow-list for Shopify webhooks.

Shopify signs the raw request body with HMAC-SHA256, base64-encodes it,
and sends it in the `X-Shopify-Hmac-Sha256` header. The signing key is
the app's `client_secret` for Custom Apps created via the Dev Dashboard
(legacy custom-app flow used the app's API secret; same effective key).

TR-3: HMAC verification runs **before any other handler logic** and uses
`hmac.compare_digest` for constant-time comparison. Failure → 401.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

# TR-13 — every topic the connector subscribes to. Receiver accepts these
# from `X-Shopify-Topic`; anything outside this set is logged-and-dropped
# rather than processed (defensive: we'd rather fail closed on a topic we
# don't know how to handle than silently treat it as one we do).
SUBSCRIBED_TOPICS: frozenset[str] = frozenset(
    {
        "orders/create",
        "orders/updated",
        "orders/paid",
        "orders/cancelled",
        "orders/fulfilled",
        "fulfillments/create",
        "fulfillments/update",
        "products/create",
        "products/update",
        "products/delete",
        "inventory_levels/update",
        "customers/create",
        "customers/update",
        "app/uninstalled",
    }
)


def compute_hmac(raw_body: bytes, secret: str) -> str:
    """Return the base64-encoded HMAC-SHA256 of `raw_body` keyed by `secret`."""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_hmac(raw_body: bytes, secret: str, header_value: str | None) -> bool:
    """Constant-time compare. Returns False on any malformed input."""
    if not header_value:
        return False
    expected = compute_hmac(raw_body, secret)
    return hmac.compare_digest(expected, header_value)


def is_known_topic(topic: str) -> bool:
    return topic in SUBSCRIBED_TOPICS
