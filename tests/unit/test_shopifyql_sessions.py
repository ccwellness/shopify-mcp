"""Tests for the ShopifyQL helper + sessions normalizer.

Together these cover the path from raw `shopifyqlQuery` payload to
domain `SessionsDay` rows. Exercises the column-name lookup (we don't
depend on positional order), missing-cell tolerance, and parseError
propagation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AnalyticsSource
from app.domain.models import StoreId
from app.shopify.normalizers.shopifyql_sessions import normalize_shopifyql_sessions
from app.shopify.shopifyql import (
    ShopifyqlColumn,
    ShopifyqlError,
    ShopifyqlResult,
    run_shopifyql,
)

LUBELIFE = StoreId(5)
PULLED_AT = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)

EXPECTED_THREE_ROWS = 3


def _result(rows: list[list[Any]]) -> ShopifyqlResult:
    return ShopifyqlResult(
        columns=(
            ShopifyqlColumn(name="day", data_type="date", display_name="Day", sub_type=None),
            ShopifyqlColumn(
                name="total_sales", data_type="money", display_name="Sales", sub_type=None
            ),
            ShopifyqlColumn(
                name="orders", data_type="number", display_name="Orders", sub_type=None
            ),
            ShopifyqlColumn(
                name="sessions", data_type="number", display_name="Sessions", sub_type=None
            ),
        ),
        rows=tuple(tuple(r) for r in rows),
    )


# ---------------------------------------------------------------------------
# run_shopifyql — envelope parsing
# ---------------------------------------------------------------------------


class _StubClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[tuple[str, str]] = []

    def query(
        self,
        store_key: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_mutation: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((store_key, query))
        return self._payload


def test_run_shopifyql_returns_typed_columns_and_rows() -> None:
    client = _StubClient(
        {
            "shopifyqlQuery": {
                "parseErrors": [],
                "tableData": {
                    "columns": [
                        {"name": "day", "dataType": "date", "displayName": "Day", "subType": None},
                        {
                            "name": "total_sales",
                            "dataType": "money",
                            "displayName": "Sales",
                            "subType": None,
                        },
                    ],
                    "rows": [["2026-05-10", "123.45"]],
                },
            }
        }
    )
    result = run_shopifyql(client, "lubelife", "FROM sales SHOW day, total_sales")
    assert result.columns[0].name == "day"
    assert result.cell(result.rows[0], "total_sales") == "123.45"


def test_run_shopifyql_raises_on_parse_errors() -> None:
    client = _StubClient({"shopifyqlQuery": {"parseErrors": [{"message": "unknown column 'foo'"}]}})
    with pytest.raises(ShopifyqlError, match="unknown column"):
        run_shopifyql(client, "lubelife", "FROM sales SHOW foo")


def test_run_shopifyql_raises_when_table_data_missing() -> None:
    client = _StubClient({"shopifyqlQuery": {"parseErrors": []}})
    with pytest.raises(ShopifyqlError, match="no tableData"):
        run_shopifyql(client, "lubelife", "FROM sales SHOW day")


def test_run_shopifyql_flattens_dict_rows_to_positional_tuples() -> None:
    # The 2026-04 Admin API returns rows as dicts keyed by column name.
    # The helper must align them positionally to `columns` so downstream
    # callers can use indexed access (`result.cell(row, name)`).
    client = _StubClient(
        {
            "shopifyqlQuery": {
                "parseErrors": [],
                "tableData": {
                    "columns": [
                        {"name": "day", "dataType": "DAY_TIMESTAMP", "displayName": "Day"},
                        {"name": "total_sales", "dataType": "MONEY", "displayName": "Sales"},
                    ],
                    "rows": [
                        {"day": "2026-05-10", "total_sales": "100.50"},
                        {"day": "2026-05-11", "total_sales": "200.00"},
                    ],
                },
            }
        }
    )
    result = run_shopifyql(client, "lubelife", "FROM sales SHOW day, total_sales")
    expected_rows = 2
    assert len(result.rows) == expected_rows
    assert result.cell(result.rows[0], "day") == "2026-05-10"
    assert result.cell(result.rows[1], "total_sales") == "200.00"


def test_run_shopifyql_wraps_query_into_graphql_doc() -> None:
    client = _StubClient(
        {"shopifyqlQuery": {"parseErrors": [], "tableData": {"columns": [], "rows": []}}}
    )
    run_shopifyql(client, "lubelife", "FROM sessions SHOW sessions SINCE -1d UNTIL today")
    assert client.calls[0][0] == "lubelife"
    doc = client.calls[0][1]
    assert "shopifyqlQuery" in doc
    assert "FROM sessions" in doc


# ---------------------------------------------------------------------------
# normalize_shopifyql_sessions
# ---------------------------------------------------------------------------


def test_normalize_happy_path() -> None:
    rows = normalize_shopifyql_sessions(
        LUBELIFE,
        _result(
            [
                ["2026-05-10", "100.50", 5, 200],
                ["2026-05-11", "250.00", 12, 480],
            ]
        ),
        pulled_at=PULLED_AT,
    )
    assert tuple(r.date for r in rows) == (date(2026, 5, 10), date(2026, 5, 11))
    assert rows[0].total_sales == Decimal("100.50")
    assert rows[0].orders == 5  # noqa: PLR2004
    assert rows[0].sessions == 200  # noqa: PLR2004
    assert rows[1].total_sales == Decimal("250.00")
    assert rows[0].source == AnalyticsSource.SHOPIFYQL
    assert rows[0].pulled_at == PULLED_AT
    # units_sold isn't in this query — KPI rollup folds it in later.
    assert rows[0].units_sold is None


def test_normalize_drops_rows_with_missing_day() -> None:
    rows = normalize_shopifyql_sessions(
        LUBELIFE,
        _result(
            [
                [None, "100.00", 5, 200],
                ["", "200.00", 6, 250],
                ["2026-05-10", "300.00", 7, 300],
            ]
        ),
        pulled_at=PULLED_AT,
    )
    assert len(rows) == 1
    assert rows[0].date == date(2026, 5, 10)


def test_normalize_tolerates_null_cells_for_metrics() -> None:
    rows = normalize_shopifyql_sessions(
        LUBELIFE,
        _result([["2026-05-10", None, None, 200]]),
        pulled_at=PULLED_AT,
    )
    assert rows[0].total_sales is None
    assert rows[0].orders is None
    assert rows[0].sessions == 200  # noqa: PLR2004


def test_normalize_empty_result_returns_empty_tuple() -> None:
    assert normalize_shopifyql_sessions(LUBELIFE, _result([]), pulled_at=PULLED_AT) == ()


def test_normalize_tolerates_missing_optional_columns() -> None:
    # Subset query: only day + sessions
    result = ShopifyqlResult(
        columns=(
            ShopifyqlColumn(name="day", data_type="date", display_name=None, sub_type=None),
            ShopifyqlColumn(name="sessions", data_type="number", display_name=None, sub_type=None),
        ),
        rows=(("2026-05-10", 99),),
    )
    rows = normalize_shopifyql_sessions(LUBELIFE, result, pulled_at=PULLED_AT)
    assert len(rows) == 1
    assert rows[0].sessions == 99  # noqa: PLR2004
    assert rows[0].orders is None
    assert rows[0].total_sales is None


def test_normalize_accepts_date_objects() -> None:
    rows = normalize_shopifyql_sessions(
        LUBELIFE,
        _result([[date(2026, 5, 10), "100.00", 5, 200]]),
        pulled_at=PULLED_AT,
    )
    assert rows[0].date == date(2026, 5, 10)


def test_normalize_drops_malformed_date_strings() -> None:
    rows = normalize_shopifyql_sessions(
        LUBELIFE,
        _result([["not-a-date", "100.00", 5, 200]]),
        pulled_at=PULLED_AT,
    )
    assert rows == ()
