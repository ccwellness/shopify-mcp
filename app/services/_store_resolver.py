"""Shared store_key → store_id resolver.

Both the webhook ingest service and the bulk sync service need to take a
store_key from config and end up with the StoreRow's auto-incremented id.
This helper auto-creates the StoreRow on first call so a fresh dev DB
doesn't need a separate seeding step.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import Store, StoreId
from app.domain.repositories import UnitOfWork
from app.shopify.config import StoreConfig


def ensure_store(uow: UnitOfWork, cfg: StoreConfig) -> StoreId:
    existing = uow.stores.get_by_key(cfg.store_key)
    if existing is not None:
        return existing.id
    now = datetime.now(tz=UTC)
    uow.stores.upsert(
        Store(
            id=StoreId(0),
            store_key=cfg.store_key,
            shop_domain=cfg.shop_domain,
            display_name=cfg.shop_domain,
            plus=cfg.plus,
            subscription_provider=cfg.subscription_provider,
            read_only=cfg.read_only,
            active=True,
            timezone=None,
            currency_code=None,
            created_at=now,
            updated_at=now,
        )
    )
    loaded = uow.stores.get_by_key(cfg.store_key)
    if loaded is None:
        raise RuntimeError(f"failed to upsert StoreRow for {cfg.store_key}")
    return loaded.id
