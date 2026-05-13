"""Unit tests for the Ensemble number-format Jinja filters.

The rules tested here come straight from the design handoff README §4
and the corresponding JS formatters in `ensemble-numbers.js`. Each rule
gets at least one positive and one negative case.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.blueprints.dashboard.filters import (
    asp,
    count,
    currency,
    date_mdy,
    datetime_pt,
    delta,
    percent,
)

MINUS = "−"  # U+2212
EMPTY = "—"  # em-dash
PT = ZoneInfo("America/Los_Angeles")


# ---------------------------------------------------------------------------
# ASP / currency — $x,xxx.xx always
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v", "expected"),
    [
        (Decimal("12"), "$12.00"),
        (Decimal("12.5"), "$12.50"),
        (Decimal("1248.5"), "$1,248.50"),
        (Decimal("284592.4"), "$284,592.40"),
        (12, "$12.00"),
        (12.5, "$12.50"),
        ("9.99", "$9.99"),
        (Decimal("-25"), f"{MINUS}$25.00"),
        (Decimal("0"), "$0.00"),
    ],
)
def test_asp_formats_currency_with_two_decimals(v: object, expected: str) -> None:
    assert asp(v) == expected


def test_currency_is_alias_for_asp() -> None:
    assert currency is asp


def test_asp_returns_em_dash_for_none() -> None:
    assert asp(None) == EMPTY


def test_asp_returns_em_dash_for_nan() -> None:
    assert asp(float("nan")) == EMPTY


def test_asp_returns_em_dash_for_garbage_string() -> None:
    assert asp("not-a-number") == EMPTY


# ---------------------------------------------------------------------------
# percent — x.x% (1 decimal) by default, x.xx% when |v| ≤ 0.1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v", "expected"),
    [
        (Decimal("12.4"), "12.4%"),
        (Decimal("2"), "2.0%"),  # trailing zero preserved
        (Decimal("2.10"), "2.1%"),
        (0, "0.00%"),  # zero hits the small-v branch
        (Decimal("0.1"), "0.10%"),  # boundary — still in small branch
        (Decimal("0.05"), "0.05%"),
        (Decimal("0.08"), "0.08%"),
        (Decimal("-2"), f"{MINUS}2.0%"),
        (Decimal("100"), "100.0%"),
    ],
)
def test_percent_formats_per_ensemble_rules(v: object, expected: str) -> None:
    assert percent(v) == expected


def test_percent_returns_em_dash_for_none() -> None:
    assert percent(None) == EMPTY


# ---------------------------------------------------------------------------
# count — x,xxx, no decimals, rounded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v", "expected"),
    [
        (1, "1"),
        (99, "99"),
        (1284, "1,284"),
        (1_988, "1,988"),
        (1_000_000, "1,000,000"),
        (Decimal("1284"), "1,284"),
        (Decimal("1284.0"), "1,284"),
        (Decimal("1284.7"), "1,285"),  # rounds
        (1284.4, "1,284"),
    ],
)
def test_count_formats_with_thousands_no_decimal(v: object, expected: str) -> None:
    assert count(v) == expected


def test_count_returns_em_dash_for_none() -> None:
    assert count(None) == EMPTY


# ---------------------------------------------------------------------------
# delta — always signed (even +); true minus U+2212 on negatives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v", "kind", "expected"),
    [
        (Decimal("12.4"), "percent", "+12.4%"),
        (Decimal("0"), "percent", "0.00%"),  # zero gets no sign per JS ref
        (Decimal("-2"), "percent", f"{MINUS}2.0%"),
        (Decimal("0.05"), "percent", "+0.05%"),
        (Decimal("2340"), "currency", "+$2,340.00"),
        (Decimal("-2340"), "currency", f"{MINUS}$2,340.00"),
    ],
)
def test_delta_signs_every_nonzero_value(v: object, kind: str, expected: str) -> None:
    assert delta(v, kind) == expected


def test_delta_returns_em_dash_for_none() -> None:
    assert delta(None) == EMPTY


# ---------------------------------------------------------------------------
# Negative-sign uses U+2212, not ASCII hyphen
# ---------------------------------------------------------------------------


def test_negative_currency_uses_true_minus_not_hyphen() -> None:
    out = asp(Decimal("-12.34"))
    assert "-" not in out  # ASCII hyphen must not appear
    assert out.startswith(MINUS)


def test_negative_percent_uses_true_minus_not_hyphen() -> None:
    out = percent(Decimal("-2"))
    assert "-" not in out
    assert out.startswith(MINUS)


# ---------------------------------------------------------------------------
# date_mdy — mm/dd/yyyy
# ---------------------------------------------------------------------------


def test_date_mdy_formats_pure_date() -> None:
    assert date_mdy(date(2026, 5, 13)) == "05/13/2026"


def test_date_mdy_uses_pt_date_for_aware_datetime() -> None:
    # 2026-05-13 02:00 UTC = 2026-05-12 19:00 PT (PDT, UTC-7).
    aware = datetime(2026, 5, 13, 2, 0, tzinfo=UTC)
    assert date_mdy(aware) == "05/12/2026"


def test_date_mdy_assumes_utc_for_naive_datetime() -> None:
    # Same UTC instant as above but TZ-naive.
    naive = datetime(2026, 5, 13, 2, 0)
    assert date_mdy(naive) == "05/12/2026"


def test_date_mdy_returns_em_dash_for_none() -> None:
    assert date_mdy(None) == EMPTY


def test_date_mdy_returns_em_dash_for_non_date_input() -> None:
    assert date_mdy("nope") == EMPTY
    assert date_mdy(42) == EMPTY


# ---------------------------------------------------------------------------
# datetime_pt — mm/dd/yyyy hh:mm AM/PM PT
# ---------------------------------------------------------------------------


def test_datetime_pt_renders_pacific_time() -> None:
    # 2026-05-13 16:35 UTC = 2026-05-13 09:35 PT (PDT, UTC-7 in May).
    aware = datetime(2026, 5, 13, 16, 35, tzinfo=UTC)
    assert datetime_pt(aware) == "05/13/2026 09:35 AM PT"


def test_datetime_pt_handles_pm_correctly() -> None:
    # 2026-05-13 23:05 UTC = 2026-05-13 16:05 PT = 04:05 PM PT.
    aware = datetime(2026, 5, 13, 23, 5, tzinfo=UTC)
    assert datetime_pt(aware) == "05/13/2026 04:05 PM PT"


def test_datetime_pt_handles_pst_offset() -> None:
    # January is PST (UTC-8). 2026-01-15 20:00 UTC = 12:00 PM PT.
    aware = datetime(2026, 1, 15, 20, 0, tzinfo=UTC)
    assert datetime_pt(aware) == "01/15/2026 12:00 PM PT"


def test_datetime_pt_assumes_utc_for_naive_datetime() -> None:
    naive = datetime(2026, 5, 13, 16, 35)
    assert datetime_pt(naive) == "05/13/2026 09:35 AM PT"


def test_datetime_pt_returns_em_dash_for_none() -> None:
    assert datetime_pt(None) == EMPTY


def test_datetime_pt_returns_em_dash_for_date_only() -> None:
    # Pure dates have no time component → can't render hh:mm.
    assert datetime_pt(date(2026, 5, 13)) == EMPTY
