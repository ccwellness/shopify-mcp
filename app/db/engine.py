"""SQLAlchemy engine and session factory.

Both the engine and the sessionmaker are module-level singletons created
lazily on first call to `get_engine()` / `get_session_factory()`. The DI
container in `app/container.py` will own these eventually; for now this
keeps Phase 1 wiring minimal.
"""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Source .env (or export it) before using the engine."
        )
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine."""
    return create_engine(_database_url(), future=True, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide sessionmaker."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session)
