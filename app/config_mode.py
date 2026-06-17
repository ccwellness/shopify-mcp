"""Data-source mode resolution for the dual-mode MCP/connector.

The connector runs in one of two modes:

- ``"db"`` — the historical default: every repository read/write goes through
  Postgres via the SQLAlchemy ``UnitOfWork``.
- ``"live"`` — no database: repositories are backed by the Shopify Admin
  GraphQL API (and OrderGroove REST), served real-time. The SQLAlchemy engine
  is never constructed in this mode.

Resolution order:

1. ``MCP_DATA_SOURCE`` env var, if set to ``db`` or ``live``, wins.
2. Otherwise: ``db`` when ``DATABASE_URL`` is set, else ``live``.

Keeping this in one tiny module (no app imports) means both ``app.container``
and the test suite can call it without pulling in the engine.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

DataSource = Literal["db", "live"]

_VALID: frozenset[str] = frozenset({"db", "live"})


def resolve_data_source(env: Mapping[str, str] | None = None) -> DataSource:
    """Return the active data source, ``"db"`` or ``"live"``.

    ``env`` defaults to ``os.environ``; tests pass a mapping to avoid mutating
    the process environment.
    """
    env = env if env is not None else os.environ
    forced = (env.get("MCP_DATA_SOURCE") or "").strip().lower()
    if forced in _VALID:
        return forced  # type: ignore[return-value]
    return "db" if env.get("DATABASE_URL") else "live"
