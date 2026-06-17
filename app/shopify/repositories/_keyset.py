"""Opaque keyset-cursor codec for live repositories.

A standalone copy of the `(sort_value, id)` base64 cursor used by the
SQLAlchemy repositories — duplicated here so `app.shopify` stays an isolated
adapter and never imports `app.repositories` (enforced by the architecture
tests). The encoding is identical, so cursors are interchangeable in shape.
"""

from __future__ import annotations

import base64
from datetime import datetime


def encode(sort_value: datetime, row_id: int) -> str:
    payload = f"{sort_value.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode(cursor: str) -> tuple[datetime, int]:
    pad = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(cursor + pad).decode()
    sort_str, row_id_str = raw.split("|", 1)
    return datetime.fromisoformat(sort_str), int(row_id_str)
