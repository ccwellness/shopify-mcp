"""Subscription provider adapters (TR-27, TR-28).

`SubscriptionProvider` is the per-store abstraction over "where do
subscription contracts live?" — Shopify's native primitives, OrderGroove's
REST API, or another third party. Every adapter writes to the same
`subscription_contracts` table so downstream consumers (dashboard, REST,
MCP, GraphQL) stay app-agnostic.

`build_provider(cfg, ...)` is the per-store dispatcher: it inspects
`cfg.subscription_provider` and returns the right adapter, or None for
stores whose credentials haven't been added yet.
"""

from __future__ import annotations

from app.services.subscriptions.base import SubscriptionProvider, build_provider

__all__ = ["SubscriptionProvider", "build_provider"]
