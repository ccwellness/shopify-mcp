"""Synthetic store-id index for live mode.

The DB assigns each store an integer primary key. Live mode has no DB, but the
MCP tools both accept and emit numeric `store_id`s. `StoreIndex` mints a
deterministic, bijective synthetic id for every configured store: the 1-based
index into the store keys sorted alphabetically. The mapping is stable for the
life of the process, so `store_id` filters and outputs round-trip correctly.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import StoreId
from app.shopify.config import StoreConfig


@dataclass(frozen=True, slots=True)
class StoreIndex:
    """Bidirectional store_key ↔ synthetic StoreId map over the configured stores."""

    _configs: dict[str, StoreConfig]
    _id_by_key: dict[str, StoreId]
    _key_by_id: dict[int, str]

    def id_for(self, store_key: str) -> StoreId | None:
        return self._id_by_key.get(store_key)

    def key_for(self, store_id: StoreId | int) -> str | None:
        return self._key_by_id.get(int(store_id))

    def config_for_key(self, store_key: str) -> StoreConfig | None:
        return self._configs.get(store_key)

    def config_for_id(self, store_id: StoreId | int) -> StoreConfig | None:
        key = self.key_for(store_id)
        return self._configs.get(key) if key else None

    def all_store_ids(self) -> tuple[StoreId, ...]:
        return tuple(self._id_by_key[k] for k in sorted(self._id_by_key))

    def store_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._id_by_key))

    def resolve_keys(self, store_ids: tuple[StoreId, ...] | None) -> tuple[str, ...]:
        """Map an optional `store_ids` filter to store keys.

        ``None`` means "all stores". Unknown ids are dropped (DB parity:
        filtering on a store that doesn't exist yields nothing for it).
        """
        if store_ids is None:
            return self.store_keys()
        keys = [self.key_for(sid) for sid in store_ids]
        return tuple(k for k in keys if k is not None)


def build_store_index(store_configs: dict[str, StoreConfig]) -> StoreIndex:
    """Assign synthetic 1-based ids to stores in sorted-key order."""
    sorted_keys = sorted(store_configs)
    id_by_key = {key: StoreId(i) for i, key in enumerate(sorted_keys, start=1)}
    key_by_id = {int(sid): key for key, sid in id_by_key.items()}
    return StoreIndex(_configs=dict(store_configs), _id_by_key=id_by_key, _key_by_id=key_by_id)
