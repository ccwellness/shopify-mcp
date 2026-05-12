"""Date input normalizer for MCP tools (TR-35).

Tools take a `str | None` from the LLM and call `parse_when(...)` to get
a Python date or datetime. Accepts ISO 8601 plus a small set of natural
phrases the LLM is likely to emit:

  today | yesterday
  Nd | N days ago        (e.g. '7d', '30 days ago')
  last_week | last_month | last_quarter | last_year
  YYYY-MM-DD             (date)
  full ISO 8601          (datetime)

`now()` is parameterizable so tests are deterministic; production callers
omit it and we use the current UTC time.

Two output flavors:
  parse_date(s, *, now=None)      -> date | None
  parse_datetime(s, *, now=None)  -> datetime | None

Both return None for empty / None input — the caller decides whether
that's "default to a window" or an error.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta


class DateParseError(ValueError):
    """Raised when a phrase cannot be parsed into a date/datetime."""


_DAYS_AGO_RE = re.compile(r"^\s*(\d+)\s*(d|days?\s*ago)\s*$", re.IGNORECASE)


def _today(now: datetime | None) -> date:
    return (now or datetime.now(tz=UTC)).date()


def parse_date(  # noqa: PLR0911 — relative-phrase branch table reads cleaner flat
    raw: str | None, *, now: datetime | None = None
) -> date | None:
    """Normalize a string to a `date`, or None if `raw` is falsy."""
    if not raw:
        return None
    phrase = raw.strip().lower()
    if not phrase:
        return None

    today = _today(now)

    if phrase == "today":
        return today
    if phrase == "yesterday":
        return today - timedelta(days=1)
    if phrase == "last_week":
        # 7 days ago — simpler than "last calendar week" and matches what
        # the LLM usually means in dashboards.
        return today - timedelta(days=7)
    if phrase == "last_month":
        return today - timedelta(days=30)
    if phrase == "last_quarter":
        return today - timedelta(days=90)
    if phrase == "last_year":
        return today - timedelta(days=365)

    m = _DAYS_AGO_RE.match(phrase)
    if m:
        return today - timedelta(days=int(m.group(1)))

    # ISO 8601: accept YYYY-MM-DD or a full datetime (we drop the time).
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise DateParseError(
            f"unrecognized date phrase: {raw!r} "
            "(expected today/yesterday/Nd/last_week/last_month/last_quarter/last_year "
            "or ISO 8601)"
        ) from exc


def parse_datetime(raw: str | None, *, now: datetime | None = None) -> datetime | None:
    """Normalize a string to a UTC `datetime`, or None if `raw` is falsy.

    Relative phrases are converted to midnight UTC of the resolved date.
    """
    if not raw:
        return None
    phrase = raw.strip()
    # Try full ISO 8601 first so callers that pass timestamps preserve precision.
    try:
        return _to_utc(datetime.fromisoformat(phrase.replace("Z", "+00:00")))
    except ValueError:
        pass

    d = parse_date(raw, now=now)
    if d is None:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
