"""ShopifyQL helper — runs a `shopifyqlQuery` and returns typed table data.

ShopifyQL is Shopify's analytics query language; it sits behind the
`shopifyqlQuery` GraphQL field. The shape that Phase 0 confirmed works
on every store we target is:

    FROM sales, sessions
    SHOW day, total_sales, orders, sessions
    GROUP BY day
    SINCE -<N>d UNTIL -1d

The response is a `tableData` envelope with `columns[]` and `rows[]`; this
module exposes it as a typed `ShopifyqlResult` so callers don't reason
about column ordering. `parseErrors` are surfaced as `ShopifyqlError`
rather than silently producing empty rows — TR-11 wants the probe to be
verifiable, not lossy.

Per TR-29, ShopifyQL has its own per-window cost budget (`shopifyqlCost`,
max 1000) separate from the standard GraphQL throttle bucket. We do NOT
parse extensions.cost here — that lives in the client's throttle layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class _SupportsQuery(Protocol):
    def query(
        self,
        store_key: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_mutation: bool = False,
    ) -> dict[str, Any]: ...


class ShopifyqlError(RuntimeError):
    """Raised when ShopifyQL returns parseErrors or no tableData."""


@dataclass(frozen=True, slots=True)
class ShopifyqlColumn:
    name: str
    data_type: str
    display_name: str | None
    sub_type: str | None


@dataclass(frozen=True, slots=True)
class ShopifyqlResult:
    """Decoded `shopifyqlQuery.tableData` envelope.

    Each row is a tuple of cell values (already Python-typed by Shopify:
    strings, numbers, etc.) aligned 1:1 to `columns`. Use `cell(row, name)`
    to grab a column by name rather than positional index.
    """

    columns: tuple[ShopifyqlColumn, ...]
    rows: tuple[tuple[Any, ...], ...]

    def index_of(self, column_name: str) -> int:
        for i, col in enumerate(self.columns):
            if col.name == column_name:
                return i
        raise KeyError(f"column {column_name!r} not in {[c.name for c in self.columns]}")

    def cell(self, row: tuple[Any, ...], column_name: str) -> Any:
        return row[self.index_of(column_name)]


def _wrap(query_str: str) -> str:
    """Wrap a raw ShopifyQL string into a `shopifyqlQuery` GraphQL document.

    Variables aren't supported on this field, so we splice the query text
    in directly — but we escape `\\` and `"` first so a stray quote in a
    label doesn't break the document. (Our query shapes are static, so
    this is defense in depth, not load-bearing.)
    """
    escaped = query_str.replace("\\", "\\\\").replace('"', '\\"')
    return (
        '{ shopifyqlQuery(query: "' + escaped + '") {'
        "  parseErrors"  # scalar [String] in this API version
        "  tableData { columns { name dataType displayName subType } rows }"
        "} }"
    )


def run_shopifyql(client: _SupportsQuery, store_key: str, query_str: str) -> ShopifyqlResult:
    """Execute a ShopifyQL query against `store_key` and return typed rows."""
    payload = client.query(store_key, _wrap(query_str))
    envelope = (payload or {}).get("shopifyqlQuery") or {}

    parse_errors = envelope.get("parseErrors") or []
    if parse_errors:
        # Shopify returns either {message:str} objects or strings; coerce.
        messages = [(e.get("message") if isinstance(e, dict) else str(e)) for e in parse_errors]
        raise ShopifyqlError(f"parseErrors for {store_key!r}: {messages}")

    table = envelope.get("tableData")
    if table is None:
        raise ShopifyqlError(f"no tableData returned for {store_key!r} (query={query_str!r})")

    columns = tuple(
        ShopifyqlColumn(
            name=str(c.get("name") or ""),
            data_type=str(c.get("dataType") or ""),
            display_name=c.get("displayName"),
            sub_type=c.get("subType"),
        )
        for c in (table.get("columns") or [])
    )

    # Shopify's `rows` is a list of dicts keyed by column name in the
    # current Admin API (2026-04). Flatten to positional tuples aligned
    # to `columns` so the result interface stays index-stable.
    column_names = [c.name for c in columns]
    raw_rows = table.get("rows") or []
    positional_rows: list[tuple[Any, ...]] = []
    for r in raw_rows:
        if isinstance(r, dict):
            positional_rows.append(tuple(r.get(name) for name in column_names))
        else:
            # Older/positional shape — accept as-is for forward/backward compat.
            positional_rows.append(tuple(r))
    return ShopifyqlResult(columns=columns, rows=tuple(positional_rows))
