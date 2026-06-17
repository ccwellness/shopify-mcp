"""Live (database-free) repository implementations.

These satisfy the same `app.domain.repositories` Protocols as the SQLAlchemy
repositories, but read from the Shopify Admin GraphQL API and OrderGroove REST
in real time. They are wired in place of the SQLAlchemy `UnitOfWork` when the
connector runs in live mode (no `DATABASE_URL`). Writes are accepted as no-ops;
sync-only methods raise `NotImplementedError`.

No module in this package may import SQLAlchemy — the architecture tests
enforce that the live path is genuinely DB-free.
"""

from __future__ import annotations

from app.shopify.repositories.store_index import StoreIndex, build_store_index
from app.shopify.repositories.unit_of_work import ShopifyUnitOfWork

__all__ = ["ShopifyUnitOfWork", "StoreIndex", "build_store_index"]
