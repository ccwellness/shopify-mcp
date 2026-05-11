"""Dashboard route handlers (TR-32).

Each view parses its query string into a service call and renders a
Jinja2 template. No SQLAlchemy here — services own all persistence
access. Errors (bad date format, inverted window) re-render the same
page with a flash-style error rather than returning JSON.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from flask import current_app, render_template, request

from app.blueprints.dashboard import bp
from app.domain.enums import FinancialStatus
from app.domain.models import LocationId, StoreId
from app.domain.specs import OrderSpec
from app.services.inventory_reporting import (
    DEFAULT_LIMIT as INV_DEFAULT_LIMIT,
)
from app.services.inventory_reporting import (
    DEFAULT_LOW_STOCK_THRESHOLD,
    InventoryReportingService,
)
from app.services.order_query import DEFAULT_LIMIT as ORDER_DEFAULT_LIMIT
from app.services.order_query import OrderQueryService
from app.services.store_compare import StoreComparisonService


def _store_comparison_service() -> StoreComparisonService:
    svc = current_app.extensions.get("store_comparison_service")
    if svc is None:
        raise RuntimeError("store_comparison_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _order_query_service() -> OrderQueryService:
    svc = current_app.extensions.get("order_query_service")
    if svc is None:
        raise RuntimeError("order_query_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _inventory_reporting_service() -> InventoryReportingService:
    svc = current_app.extensions.get("inventory_reporting_service")
    if svc is None:
        raise RuntimeError("inventory_reporting_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _parse_optional_dt(raw: str | None) -> tuple[datetime | None, str | None]:
    """Return (datetime, error_message). Empty/missing → (None, None)."""
    if not raw:
        return None, None
    try:
        return datetime.fromisoformat(raw), None
    except ValueError:
        return None, f"Invalid ISO 8601 datetime: {raw!r}"


def _parse_optional_int(raw: str | None, field: str) -> tuple[int | None, str | None]:
    if not raw:
        return None, None
    try:
        return int(raw), None
    except ValueError:
        return None, f"{field} must be an integer (got {raw!r})"


def _parse_store_ids(raw_values: list[str]) -> tuple[tuple[StoreId, ...] | None, str | None]:
    if not raw_values:
        return None, None
    out: list[StoreId] = []
    for v in raw_values:
        try:
            out.append(StoreId(int(v)))
        except ValueError:
            return None, f"store_id must be an integer (got {v!r})"
    return tuple(out), None


# ---------------------------------------------------------------------------
# Home — landing page with nav cards
# ---------------------------------------------------------------------------


@bp.get("/")
def home() -> str:
    return render_template("dashboard/home.html")


# ---------------------------------------------------------------------------
# Cross-store comparison
# ---------------------------------------------------------------------------


@bp.get("/compare")
def compare() -> str:
    # Default window: trailing 7 days, since-inclusive / until-exclusive.
    now = datetime.now(tz=UTC).replace(microsecond=0)
    default_until = now
    default_since = now - timedelta(days=7)

    since_raw = request.args.get("since") or default_since.strftime("%Y-%m-%dT%H:%M:%SZ")
    until_raw = request.args.get("until") or default_until.strftime("%Y-%m-%dT%H:%M:%SZ")
    store_id_raw = request.args.getlist("store_id")

    errors: list[str] = []
    since, err = _parse_optional_dt(since_raw)
    if err:
        errors.append(err)
    until, err = _parse_optional_dt(until_raw)
    if err:
        errors.append(err)
    store_ids, err = _parse_store_ids(store_id_raw)
    if err:
        errors.append(err)

    comparison: Any = None
    if not errors and since is not None and until is not None:
        try:
            comparison = _store_comparison_service().compare_orders(
                since=since, until=until, store_ids=store_ids
            )
        except ValueError as exc:
            errors.append(str(exc))

    return render_template(
        "dashboard/compare.html",
        since_raw=since_raw,
        until_raw=until_raw,
        store_id_raw=store_id_raw,
        comparison=comparison,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Cross-store orders list
# ---------------------------------------------------------------------------


@bp.get("/orders")
def orders() -> str:
    return _render_orders(partial=False)


@bp.get("/orders/rows")
def orders_rows() -> str:
    """HTMX endpoint — returns just the next page of rows as an HTML fragment."""
    return _render_orders(partial=True)


def _render_orders(*, partial: bool) -> str:
    since_raw = request.args.get("since") or ""
    until_raw = request.args.get("until") or ""
    status_raw = request.args.get("financial_status") or ""
    sku_raw = request.args.get("sku") or ""
    store_id_raw = request.args.getlist("store_id")
    cursor = request.args.get("cursor") or None

    errors: list[str] = []
    since, err = _parse_optional_dt(since_raw)
    if err:
        errors.append(err)
    until, err = _parse_optional_dt(until_raw)
    if err:
        errors.append(err)
    store_ids, err = _parse_store_ids(store_id_raw)
    if err:
        errors.append(err)

    financial_status: FinancialStatus | None = None
    if status_raw:
        try:
            financial_status = FinancialStatus(status_raw)
        except ValueError:
            errors.append(f"financial_status invalid: {status_raw!r}")

    page = None
    if not errors:
        spec = OrderSpec(
            store_ids=store_ids,
            since=since,
            until=until,
            financial_status=financial_status,
            sku=sku_raw or None,
        )
        page = _order_query_service().list_orders(spec, limit=ORDER_DEFAULT_LIMIT, cursor=cursor)

    template = "dashboard/_orders_rows.html" if partial else "dashboard/orders.html"
    return render_template(
        template,
        since_raw=since_raw,
        until_raw=until_raw,
        status_raw=status_raw,
        sku_raw=sku_raw,
        store_id_raw=store_id_raw,
        financial_statuses=[s.value for s in FinancialStatus],
        page=page,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Low-stock inventory
# ---------------------------------------------------------------------------


@bp.get("/inventory/low-stock")
def low_stock() -> str:
    threshold_raw = request.args.get("threshold") or str(DEFAULT_LOW_STOCK_THRESHOLD)
    location_id_raw = request.args.get("location_id") or ""
    sku_raw = request.args.get("sku") or ""
    store_id_raw = request.args.getlist("store_id")
    cursor = request.args.get("cursor") or None

    errors: list[str] = []
    threshold, err = _parse_optional_int(threshold_raw, "threshold")
    if err:
        errors.append(err)
    if threshold is not None and threshold < 0:
        errors.append("threshold must be non-negative")
    location_id, err = _parse_optional_int(location_id_raw, "location_id")
    if err:
        errors.append(err)
    store_ids, err = _parse_store_ids(store_id_raw)
    if err:
        errors.append(err)

    page = None
    if not errors:
        page = _inventory_reporting_service().list_low_stock(
            store_ids=store_ids,
            threshold=threshold if threshold is not None else DEFAULT_LOW_STOCK_THRESHOLD,
            location_id=LocationId(location_id) if location_id is not None else None,
            sku=sku_raw or None,
            limit=INV_DEFAULT_LIMIT,
            cursor=cursor,
        )

    return render_template(
        "dashboard/low_stock.html",
        threshold_raw=threshold_raw,
        location_id_raw=location_id_raw,
        sku_raw=sku_raw,
        store_id_raw=store_id_raw,
        page=page,
        errors=errors,
    )
