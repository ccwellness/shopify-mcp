"""SubscriptionProvider Protocol + per-store dispatcher (TR-27).

Each adapter is constructed once per sync run with its `store_id`, a
customer-lookup closure (Shopify-legacy-id → local CustomerId), and any
provider-specific HTTP client. `iter_active()` yields domain
`SubscriptionContract` records ready to upsert.

The dispatcher reads `StoreConfig.subscription_provider` to choose:

  ORDERGROOVE → `OrderGrooveProvider` (requires `ordergroove_api_key`)
  NATIVE      → not implemented yet (Phase 0 probe returned 0 contracts
                on every target store; will build when a store switches)
  UNKNOWN     → returns None — sync_subscriptions treats as 'skip'
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Protocol, runtime_checkable

from app.domain.enums import SubscriptionProvider as SubscriptionProviderEnum
from app.domain.models import CustomerId, StoreId, SubscriptionContract
from app.shopify.config import StoreConfig


@runtime_checkable
class SubscriptionProvider(Protocol):
    """One adapter per store. Provides every active subscription as a
    normalized `SubscriptionContract` ready for upsert."""

    def iter_active(self) -> Iterator[SubscriptionContract]: ...


CustomerLookup = Callable[[str], CustomerId | None]
"""Maps a Shopify customer numeric id (string) → local CustomerId, or None
when no local customer row exists yet (sync ordering: customers first,
then subscriptions)."""


class UnknownProviderError(RuntimeError):
    """A store with `subscription_provider=NATIVE` was requested before the
    NativeProvider exists. Should be unreachable in v1 since all three target
    stores use OrderGroove."""


def build_provider(
    cfg: StoreConfig,
    store_id: StoreId,
    customer_lookup: CustomerLookup,
) -> SubscriptionProvider | None:
    """Return a configured provider for `cfg`, or None if not provisioned.

    `None` means the dispatcher saw a stuck-pending state — e.g. the store
    is flagged as ORDERGROOVE but no API key in `.env` yet. SyncService
    treats this as "skip subscriptions for this store" rather than an
    error so partial dev setups stay running.
    """
    if cfg.subscription_provider is SubscriptionProviderEnum.ORDERGROOVE:
        if not cfg.ordergroove_api_key:
            return None
        # Local import: keeps the OG http client out of L4's module-level
        # imports for stores that don't use it (and the architecture test).
        from app.integrations.ordergroove.client import OrderGrooveClient  # noqa: PLC0415
        from app.services.subscriptions.ordergroove import (  # noqa: PLC0415
            OrderGrooveProvider,
        )

        client = OrderGrooveClient(cfg.ordergroove_api_key)
        return OrderGrooveProvider(
            client=client, store_id=store_id, customer_lookup=customer_lookup
        )

    if cfg.subscription_provider is SubscriptionProviderEnum.NATIVE:
        raise UnknownProviderError(
            f"NativeProvider not built yet — store {cfg.store_key!r} is flagged "
            "subscription_provider=native but no native adapter exists."
        )

    # UNKNOWN or otherwise unconfigured: silent skip.
    return None
