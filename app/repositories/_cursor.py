"""Opaque cursor helpers shared across repositories.

We use keyset pagination on (sort_key, id) — both DESC — encoded as
base64(`<sort_key_iso>|<id>`). Encoding is intentionally trivial; callers
treat cursors as opaque strings.
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
