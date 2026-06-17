"""Live StoreRepository — stores come from config, not the DB."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import Store, StoreId
from app.shopify.config import StoreConfig
from app.shopify.repositories.store_index import StoreIndex


def _to_store(store_id: StoreId, cfg: StoreConfig, *, now: datetime) -> Store:
    return Store(
        id=store_id,
        store_key=cfg.store_key,
        shop_domain=cfg.shop_domain,
        display_name=cfg.store_key,
        plus=cfg.plus,
        subscription_provider=cfg.subscription_provider,
        read_only=cfg.read_only,
        active=True,
        timezone=None,
        currency_code=None,
        created_at=now,
        updated_at=now,
    )


class LiveStoreRepository:
    def __init__(self, index: StoreIndex) -> None:
        self._index = index

    def list_active(self) -> tuple[Store, ...]:
        now = datetime.now(tz=UTC)
        out = []
        for store_id in self._index.all_store_ids():
            cfg = self._index.config_for_id(store_id)
            if cfg is not None:
                out.append(_to_store(store_id, cfg, now=now))
        return tuple(out)

    def get(self, store_id: StoreId) -> Store | None:
        cfg = self._index.config_for_id(store_id)
        if cfg is None:
            return None
        return _to_store(store_id, cfg, now=datetime.now(tz=UTC))

    def get_by_key(self, store_key: str) -> Store | None:
        sid = self._index.id_for(store_key)
        cfg = self._index.config_for_key(store_key)
        if sid is None or cfg is None:
            return None
        return _to_store(sid, cfg, now=datetime.now(tz=UTC))

    def get_by_domain(self, shop_domain: str) -> Store | None:
        for store_id in self._index.all_store_ids():
            cfg = self._index.config_for_id(store_id)
            if cfg is not None and cfg.shop_domain == shop_domain:
                return _to_store(store_id, cfg, now=datetime.now(tz=UTC))
        return None

    def upsert(self, store: Store) -> None:  # noqa: ARG002 — stores are config-defined in live mode
        return None
