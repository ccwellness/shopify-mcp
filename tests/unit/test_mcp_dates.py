"""Unit tests for `mcp_server.dates.parse_date` / `parse_datetime` (TR-35)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from mcp_server.dates import DateParseError, parse_date, parse_datetime

NOW = datetime(2026, 5, 12, 14, 30, tzinfo=UTC)
TODAY = date(2026, 5, 12)


# ---------------------------------------------------------------------------
# parse_date — relative phrases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("today", date(2026, 5, 12)),
        ("Today", date(2026, 5, 12)),
        ("yesterday", date(2026, 5, 11)),
        ("YESTERDAY", date(2026, 5, 11)),
        ("last_week", date(2026, 5, 5)),
        ("last_month", date(2026, 4, 12)),
        ("last_quarter", date(2026, 2, 11)),
        ("last_year", date(2025, 5, 12)),
        ("7d", date(2026, 5, 5)),
        ("30d", date(2026, 4, 12)),
        ("1d", date(2026, 5, 11)),
        ("3 days ago", date(2026, 5, 9)),
        ("3 day ago", date(2026, 5, 9)),
    ],
)
def test_parse_date_relative_phrases(raw: str, expected: date) -> None:
    assert parse_date(raw, now=NOW) == expected


def test_parse_date_iso_yyyy_mm_dd() -> None:
    assert parse_date("2026-04-30", now=NOW) == date(2026, 4, 30)


def test_parse_date_full_iso_datetime_drops_time() -> None:
    assert parse_date("2026-04-30T15:00:00Z", now=NOW) == date(2026, 4, 30)


def test_parse_date_empty_returns_none() -> None:
    assert parse_date("", now=NOW) is None
    assert parse_date(None, now=NOW) is None
    assert parse_date("   ", now=NOW) is None


def test_parse_date_unknown_phrase_raises() -> None:
    with pytest.raises(DateParseError, match="unrecognized date phrase"):
        parse_date("next tuesday-ish", now=NOW)


def test_parse_date_uses_now_default_when_omitted() -> None:
    # Smoke: default `now` works (we don't pin the value, just verify no crash).
    result = parse_date("today")
    assert isinstance(result, date)


# ---------------------------------------------------------------------------
# parse_datetime
# ---------------------------------------------------------------------------


def test_parse_datetime_iso_8601_with_z() -> None:
    dt = parse_datetime("2026-04-30T15:00:00Z", now=NOW)
    assert dt == datetime(2026, 4, 30, 15, 0, tzinfo=UTC)


def test_parse_datetime_iso_8601_with_offset() -> None:
    dt = parse_datetime("2026-04-30T15:00:00+00:00", now=NOW)
    assert dt == datetime(2026, 4, 30, 15, 0, tzinfo=UTC)


def test_parse_datetime_naive_iso_assumes_utc() -> None:
    dt = parse_datetime("2026-04-30T15:00:00", now=NOW)
    assert dt == datetime(2026, 4, 30, 15, 0, tzinfo=UTC)


def test_parse_datetime_relative_phrase_returns_midnight_utc() -> None:
    dt = parse_datetime("yesterday", now=NOW)
    assert dt == datetime(2026, 5, 11, 0, 0, tzinfo=UTC)


def test_parse_datetime_empty_returns_none() -> None:
    assert parse_datetime("", now=NOW) is None
    assert parse_datetime(None, now=NOW) is None
