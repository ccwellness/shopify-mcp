"""Ensemble-spec number formatters, registered as Jinja filters.

Mirrors `design_handoff_ensemble_theme/ensemble-numbers.js` so the
server-rendered dashboard and any future client-side renderer agree
on output. Every formatter returns the canonical em-dash placeholder
`—` for None / NaN / unparseable input.

Rules (from the design handoff README §4):

  - ASP / currency      → $x,xxx.xx       — always 2 decimals + thousands
  - percent (default)   → x.x%            — 1 decimal, trailing 0 preserved
  - percent (small)     → x.xx%           — 2 decimals when |v| ≤ 0.1
  - count               → x,xxx           — thousands, no decimals
  - delta               → +x.x% / −$x.xx  — signed, true minus U+2212
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any

from flask import Blueprint

MINUS = "−"  # U+2212 MINUS SIGN — wider than hyphen, vertically centered
EMPTY = "—"  # em-dash, canonical empty placeholder
_SMALL_PERCENT_CUTOFF = Decimal("0.1")


def _to_decimal(v: Any) -> Decimal | None:  # noqa: PLR0911 — each branch handles a distinct input type
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v if v.is_finite() else None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return Decimal(str(v))
    if isinstance(v, int):
        return Decimal(v)
    try:
        d = Decimal(str(v))
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def asp(v: Any) -> str:
    """`$x,xxx.xx` — always 2 decimals, thousands separators."""
    d = _to_decimal(v)
    if d is None:
        return EMPTY
    sign = MINUS if d < 0 else ""
    return f"{sign}${abs(d):,.2f}"


# Currency totals share the ASP shape per design.md §4.
currency = asp


def percent(v: Any) -> str:
    """`x.x%` (default) or `x.xx%` when |v| ≤ 0.1.

    Input is a percent value: pass `12.4` to render `12.4%`, not `0.124`.
    """
    d = _to_decimal(v)
    if d is None:
        return EMPTY
    sign = MINUS if d < 0 else ""
    abs_v = abs(d)
    decimals = 2 if abs_v <= _SMALL_PERCENT_CUTOFF else 1
    return f"{sign}{abs_v:.{decimals}f}%"


def count(v: Any) -> str:
    """`x,xxx` — thousands separators, no decimals. Rounds non-integers."""
    d = _to_decimal(v)
    if d is None:
        return EMPTY
    return f"{int(d.to_integral_value()):,}"


def delta(v: Any, kind: str = "percent") -> str:
    """`+x.x%` / `−$x.xx` — always signed (even `+`)."""
    d = _to_decimal(v)
    if d is None:
        return EMPTY
    sign = "+" if d > 0 else (MINUS if d < 0 else "")
    abs_v = abs(d)
    if kind == "percent":
        decimals = 2 if abs_v <= _SMALL_PERCENT_CUTOFF else 1
        return f"{sign}{abs_v:.{decimals}f}%"
    return f"{sign}${abs_v:,.2f}"


def register(bp: Blueprint) -> None:
    """Attach the formatters as app-wide Jinja filters via the blueprint."""
    bp.add_app_template_filter(asp, name="asp")
    bp.add_app_template_filter(currency, name="currency")
    bp.add_app_template_filter(percent, name="percent")
    bp.add_app_template_filter(count, name="count")
    bp.add_app_template_filter(delta, name="delta")
