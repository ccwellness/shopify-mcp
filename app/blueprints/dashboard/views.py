"""Dashboard route handlers (TR-32).

Each view parses its query string into a service call and renders a
Jinja2 template. No SQLAlchemy here — services own all persistence
access. Errors (bad date format, inverted window) re-render the same
page with a flash-style error rather than returning JSON.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import (
    current_app,
    flash,
    get_flashed_messages,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response as WerkzeugResponse

from app.blueprints.dashboard import bp
from app.domain.enums import (
    FinancialStatus,
    ProductStatus,
    SubscriptionProvider,
    SubscriptionStatus,
)
from app.domain.models import (
    ApiTokenId,
    CustomerId,
    LocationId,
    OrderId,
    ProductId,
    StoreId,
    SubscriptionContractId,
)
from app.domain.specs import OrderSpec, ProductSpec, SubscriptionSpec
from app.services.analytics import AnalyticsService
from app.services.auth import AuthService
from app.services.inventory_reporting import (
    DEFAULT_LIMIT as INV_DEFAULT_LIMIT,
)
from app.services.inventory_reporting import (
    DEFAULT_LOW_STOCK_THRESHOLD,
    InventoryReportingService,
)
from app.services.order_query import DEFAULT_LIMIT as ORDER_DEFAULT_LIMIT
from app.services.order_query import OrderQueryService
from app.services.product_query import (
    DEFAULT_LIMIT as PRODUCT_DEFAULT_LIMIT,
)
from app.services.product_query import ProductQueryService
from app.services.store_compare import StoreComparisonService
from app.services.store_query import StoreQueryService
from app.services.subscription_query import (
    DEFAULT_LIMIT as SUB_DEFAULT_LIMIT,
)
from app.services.subscription_query import SubscriptionQueryService

_PT = ZoneInfo("America/Los_Angeles")


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


def _auth_service() -> AuthService:
    svc = current_app.extensions.get("auth_service")
    if svc is None:
        raise RuntimeError("auth_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _store_query_service() -> StoreQueryService:
    svc = current_app.extensions.get("store_query_service")
    if svc is None:
        raise RuntimeError("store_query_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _analytics_service() -> AnalyticsService:
    svc = current_app.extensions.get("analytics_service")
    if svc is None:
        raise RuntimeError("analytics_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _subscription_query_service() -> SubscriptionQueryService:
    svc = current_app.extensions.get("subscription_query_service")
    if svc is None:
        raise RuntimeError("subscription_query_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _product_query_service() -> ProductQueryService:
    svc = current_app.extensions.get("product_query_service")
    if svc is None:
        raise RuntimeError("product_query_service is not wired on this app")
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
# Session auth — gate every dashboard route behind a token-backed login
# ---------------------------------------------------------------------------

_OPEN_ENDPOINTS = {
    "dashboard.login",
    "dashboard.login_submit",
    "dashboard.logout",
    "dashboard.static",
}


def _safe_next(raw: str | None) -> str:
    """Return `raw` only if it's a safe in-app relative URL; else `/`.

    Defends against open-redirect attacks where a crafted `next=` param
    points off-site (e.g. `next=//evil.example.com`). A safe value:
      - starts with exactly one `/` (no `//` netloc, no scheme)
      - has no scheme or netloc when parsed
    """
    fallback = url_for("dashboard.home")
    if not raw:
        return fallback
    if not raw.startswith("/") or raw.startswith("//"):
        return fallback
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return fallback
    return raw


@bp.before_request
def _require_login() -> WerkzeugResponse | None:
    """Gate every dashboard endpoint except login/logout behind session auth."""
    if request.endpoint in _OPEN_ENDPOINTS:
        return None
    if session.get("token_id"):
        return None
    # Preserve the original target so post-login we send the user back.
    next_url = request.full_path if request.method == "GET" else request.path
    return redirect(url_for("dashboard.login", next=next_url), code=HTTPStatus.FOUND)


@bp.get("/login")
def login() -> str | WerkzeugResponse:
    """Render the login form. If already logged in, bounce to next or home."""
    if session.get("token_id"):
        return redirect(_safe_next(request.args.get("next")), code=HTTPStatus.FOUND)
    return render_template(
        "dashboard/login.html",
        next_url=request.args.get("next") or "",
        errors=get_flashed_messages(category_filter=["login_error"]),
    )


@bp.post("/login")
def login_submit() -> WerkzeugResponse:
    plaintext = (request.form.get("token") or "").strip()
    if not plaintext:
        flash("token is required", category="login_error")
        return redirect(url_for("dashboard.login"), code=HTTPStatus.SEE_OTHER)
    token = _auth_service().validate(plaintext)
    if token is None:
        flash("invalid or expired token", category="login_error")
        return redirect(url_for("dashboard.login"), code=HTTPStatus.SEE_OTHER)
    session.clear()  # avoid session-fixation by minting a fresh session
    session["token_id"] = int(token.id)
    session["token_name"] = token.name
    return redirect(
        _safe_next(request.form.get("next") or request.args.get("next")),
        code=HTTPStatus.SEE_OTHER,
    )


@bp.get("/logout")
@bp.post("/logout")
def logout() -> WerkzeugResponse:
    session.clear()
    return redirect(url_for("dashboard.login"), code=HTTPStatus.SEE_OTHER)


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
    # Default window: trailing 7 days through today (inclusive).
    today = datetime.now(tz=UTC).date()
    default_since = today - timedelta(days=6)
    default_until = today

    since_raw = request.args.get("since") or default_since.isoformat()
    until_raw = request.args.get("until") or default_until.isoformat()
    store_id_raw = request.args.getlist("store_id")

    errors: list[str] = []
    since_date, err = _parse_optional_date(since_raw)
    if err:
        errors.append(err)
    until_date, err = _parse_optional_date(until_raw)
    if err:
        errors.append(err)
    store_ids, err = _parse_store_ids(store_id_raw)
    if err:
        errors.append(err)

    comparison: Any = None
    if not errors and since_date is not None and until_date is not None:
        # User picks PT calendar dates; treat the window as PT-midnight to
        # PT-midnight half-open (until-date inclusive). Convert to UTC for
        # the service / SQL layer.
        since_dt = datetime.combine(since_date, datetime.min.time(), tzinfo=_PT).astimezone(UTC)
        until_dt = datetime.combine(
            until_date + timedelta(days=1), datetime.min.time(), tzinfo=_PT
        ).astimezone(UTC)
        try:
            comparison = _store_comparison_service().compare_orders(
                since=since_dt, until=until_dt, store_ids=store_ids
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


@bp.get("/orders/<int:order_id>")
def order_detail(order_id: int) -> tuple[str, int] | str:
    svc = _order_query_service()
    order = svc.get_order_by_id(OrderId(order_id))
    if order is None:
        return render_template(
            "dashboard/not_found.html", what=f"Order {order_id}"
        ), HTTPStatus.NOT_FOUND
    refunds = svc.list_refunds_for_order(OrderId(order_id))
    return render_template("dashboard/order_detail.html", order=order, refunds=refunds)


def _render_orders(*, partial: bool) -> str:
    since_raw = request.args.get("since") or ""
    until_raw = request.args.get("until") or ""
    status_raw = request.args.get("financial_status") or ""
    sku_raw = request.args.get("sku") or ""
    store_id_raw = request.args.getlist("store_id")
    cursor = request.args.get("cursor") or None

    errors: list[str] = []
    since_date, err = _parse_optional_date(since_raw)
    if err:
        errors.append(err)
    until_date, err = _parse_optional_date(until_raw)
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

    # User picks PT calendar dates; convert to half-open UTC datetime
    # window with the until-date inclusive (until → next PT midnight).
    since_dt = (
        datetime.combine(since_date, datetime.min.time(), tzinfo=_PT).astimezone(UTC)
        if since_date is not None
        else None
    )
    until_dt = (
        datetime.combine(
            until_date + timedelta(days=1), datetime.min.time(), tzinfo=_PT
        ).astimezone(UTC)
        if until_date is not None
        else None
    )

    page = None
    if not errors:
        spec = OrderSpec(
            store_ids=store_ids,
            since=since_dt,
            until=until_dt,
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


# ---------------------------------------------------------------------------
# Analytics — per (store, day) KPI table (TR-31)
# ---------------------------------------------------------------------------


def _parse_optional_date(raw: str | None) -> tuple[date | None, str | None]:
    if not raw:
        return None, None
    try:
        return date.fromisoformat(raw), None
    except ValueError:
        return None, f"Invalid date (YYYY-MM-DD): {raw!r}"


@bp.get("/analytics")
def analytics() -> str:
    # Default window: trailing 7 days, ending yesterday (today is incomplete).
    today = datetime.now(tz=UTC).date()
    default_until = today - timedelta(days=1)
    default_since = default_until - timedelta(days=6)

    since_raw = request.args.get("since") or default_since.isoformat()
    until_raw = request.args.get("until") or default_until.isoformat()
    store_id_raw = request.args.getlist("store_id")

    errors: list[str] = []
    since, err = _parse_optional_date(since_raw)
    if err:
        errors.append(err)
    until, err = _parse_optional_date(until_raw)
    if err:
        errors.append(err)
    store_ids, err = _parse_store_ids(store_id_raw)
    if err:
        errors.append(err)

    rows: tuple = ()
    if not errors and since is not None and until is not None:
        try:
            rows = _analytics_service().list_kpis(store_ids=store_ids, since=since, until=until)
        except ValueError as exc:
            errors.append(str(exc))

    stores = _store_query_service().list_active()
    return render_template(
        "dashboard/analytics.html",
        since_raw=since_raw,
        until_raw=until_raw,
        store_id_raw=store_id_raw,
        rows=rows,
        stores=stores,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Subscriptions — per-contract list across stores + providers
# ---------------------------------------------------------------------------


@bp.get("/subscriptions")
def subscriptions() -> str:
    return _render_subscriptions(partial=False)


@bp.get("/subscriptions/rows")
def subscriptions_rows() -> str:
    """HTMX endpoint — returns just the next page of rows as an HTML fragment."""
    return _render_subscriptions(partial=True)


@bp.get("/subscriptions/<int:contract_id>")
def subscription_detail(contract_id: int) -> tuple[str, int] | str:
    contract = _subscription_query_service().get_by_id(SubscriptionContractId(contract_id))
    if contract is None:
        return render_template(
            "dashboard/not_found.html", what=f"Subscription {contract_id}"
        ), HTTPStatus.NOT_FOUND
    return render_template("dashboard/subscription_detail.html", c=contract)


def _render_subscriptions(*, partial: bool) -> str:
    store_id_raw = request.args.getlist("store_id")
    status_raw = request.args.get("status") or ""
    provider_raw = request.args.get("provider") or ""
    customer_id_raw = request.args.get("customer_id") or ""
    cursor = request.args.get("cursor") or None

    errors: list[str] = []
    store_ids, err = _parse_store_ids(store_id_raw)
    if err:
        errors.append(err)
    customer_id, err = _parse_optional_int(customer_id_raw, "customer_id")
    if err:
        errors.append(err)

    status: SubscriptionStatus | None = None
    if status_raw:
        try:
            status = SubscriptionStatus(status_raw)
        except ValueError:
            errors.append(f"status invalid: {status_raw!r}")

    provider: SubscriptionProvider | None = None
    if provider_raw:
        try:
            provider = SubscriptionProvider(provider_raw)
        except ValueError:
            errors.append(f"provider invalid: {provider_raw!r}")

    page = None
    if not errors:
        spec = SubscriptionSpec(
            store_ids=store_ids,
            customer_id=CustomerId(customer_id) if customer_id is not None else None,
            status=status,
            provider=provider,
        )
        page = _subscription_query_service().list_subscriptions(
            spec, limit=SUB_DEFAULT_LIMIT, cursor=cursor
        )

    template = "dashboard/_subscriptions_rows.html" if partial else "dashboard/subscriptions.html"
    return render_template(
        template,
        store_id_raw=store_id_raw,
        status_raw=status_raw,
        provider_raw=provider_raw,
        customer_id_raw=customer_id_raw,
        statuses=[s.value for s in SubscriptionStatus],
        providers=[p.value for p in SubscriptionProvider],
        page=page,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Products — list + per-product analytics drilldown
# ---------------------------------------------------------------------------

_PRODUCT_SALES_WINDOW_DAYS = 30


@bp.get("/products")
def products() -> str:
    return _render_products(partial=False)


@bp.get("/products/rows")
def products_rows() -> str:
    """HTMX endpoint — returns just the next page of rows as an HTML fragment."""
    return _render_products(partial=True)


def _render_products(*, partial: bool) -> str:
    store_id_raw = request.args.getlist("store_id")
    status_raw = request.args.get("status") or ""
    vendor_raw = request.args.get("vendor") or ""
    type_raw = request.args.get("product_type") or ""
    title_raw = request.args.get("title_query") or ""
    cursor = request.args.get("cursor") or None

    errors: list[str] = []
    store_ids, err = _parse_store_ids(store_id_raw)
    if err:
        errors.append(err)

    status: ProductStatus | None = None
    if status_raw:
        try:
            status = ProductStatus(status_raw)
        except ValueError:
            errors.append(f"status invalid: {status_raw!r}")

    page = None
    if not errors:
        spec = ProductSpec(
            store_ids=store_ids,
            status=status,
            title_query=title_raw or None,
            vendor=vendor_raw or None,
            product_type=type_raw or None,
        )
        page = _product_query_service().list_products(
            spec, limit=PRODUCT_DEFAULT_LIMIT, cursor=cursor
        )

    template = "dashboard/_products_rows.html" if partial else "dashboard/products.html"
    return render_template(
        template,
        store_id_raw=store_id_raw,
        status_raw=status_raw,
        vendor_raw=vendor_raw,
        type_raw=type_raw,
        title_raw=title_raw,
        statuses=[s.value for s in ProductStatus],
        page=page,
        errors=errors,
    )


@bp.get("/products/<int:product_id>")
def product_detail(product_id: int) -> tuple[str, int] | str:
    svc = _product_query_service()
    product = svc.get_product_by_id(ProductId(product_id))
    if product is None:
        return render_template(
            "dashboard/not_found.html", what=f"Product {product_id}"
        ), HTTPStatus.NOT_FOUND

    # Trailing 30 days [since, until), in UTC. Configurable via ?since=&until=.
    now = datetime.now(tz=UTC).replace(microsecond=0)
    until_raw = request.args.get("until") or ""
    since_raw = request.args.get("since") or ""
    errors: list[str] = []
    until, err = _parse_optional_dt(until_raw)
    if err:
        errors.append(err)
    since, err = _parse_optional_dt(since_raw)
    if err:
        errors.append(err)
    if until is None:
        until = now
    if since is None:
        since = until - timedelta(days=_PRODUCT_SALES_WINDOW_DAYS)

    variant_ids = tuple(v.id for v in product.variants)
    levels = svc.get_inventory_for_variants(product.store_id, variant_ids) if variant_ids else ()
    sales_series = svc.get_sales_by_day(product.store_id, ProductId(product_id), since, until)
    recent = svc.get_recent_orders(product.store_id, ProductId(product_id))
    locations = svc.get_locations(product.store_id)
    location_names = {loc.id: loc.name for loc in locations}

    # Zero-fill the daily series so the trend table is continuous.
    sales_by_date = {d.date: d for d in sales_series}
    days: list[dict[str, Any]] = []
    cursor_day = since.date()
    end_day = until.date()
    while cursor_day < end_day:
        hit = sales_by_date.get(cursor_day)
        days.append(
            {
                "date": cursor_day,
                "units": hit.units if hit else 0,
                "gross_revenue": hit.gross_revenue if hit else Decimal("0"),
                "order_count": hit.order_count if hit else 0,
            }
        )
        cursor_day = cursor_day + timedelta(days=1)

    totals = {
        "units": sum(d["units"] for d in days),
        "gross_revenue": sum((d["gross_revenue"] for d in days), Decimal("0")),
        "orders": sum(d["order_count"] for d in days),
    }

    return render_template(
        "dashboard/product_detail.html",
        product=product,
        levels=levels,
        location_names=location_names,
        sales_days=days,
        totals=totals,
        since=since,
        until=until,
        recent=recent,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Admin — API token management (TR-4)
# ---------------------------------------------------------------------------


@bp.get("/admin/tokens")
def tokens_list() -> str:
    """List active tokens + mint form + revealed-once plaintext (via flash)."""
    tokens = _auth_service().list_active()
    stores = _store_query_service().list_active()
    # Plaintext is flashed once after mint; consume here so it appears once.
    revealed = get_flashed_messages(category_filter=["minted_plaintext"])
    return render_template(
        "dashboard/tokens.html",
        tokens=tokens,
        stores=stores,
        revealed_plaintext=revealed[0] if revealed else None,
        revealed_name=(get_flashed_messages(category_filter=["minted_name"]) or [None])[0],
        errors=get_flashed_messages(category_filter=["error"]),
    )


@bp.post("/admin/tokens/mint")
def tokens_mint() -> WerkzeugResponse:
    name = (request.form.get("name") or "").strip()
    store_id_raw = (request.form.get("store_id") or "").strip()
    expires_days_raw = (request.form.get("expires_days") or "").strip()

    if not name:
        flash("name is required", category="error")
        return redirect(url_for("dashboard.tokens_list"), code=HTTPStatus.SEE_OTHER)

    store_id: StoreId | None = None
    if store_id_raw:
        try:
            store_id = StoreId(int(store_id_raw))
        except ValueError:
            flash(f"store_id must be an integer (got {store_id_raw!r})", category="error")
            return redirect(url_for("dashboard.tokens_list"), code=HTTPStatus.SEE_OTHER)

    expires_at: datetime | None = None
    if expires_days_raw:
        try:
            days = int(expires_days_raw)
        except ValueError:
            flash(f"expires_days must be an integer (got {expires_days_raw!r})", category="error")
            return redirect(url_for("dashboard.tokens_list"), code=HTTPStatus.SEE_OTHER)
        if days <= 0:
            flash("expires_days must be > 0", category="error")
            return redirect(url_for("dashboard.tokens_list"), code=HTTPStatus.SEE_OTHER)
        expires_at = datetime.now(tz=UTC) + timedelta(days=days)

    _, plaintext = _auth_service().mint(name=name, store_id=store_id, expires_at=expires_at)
    flash(plaintext, category="minted_plaintext")
    flash(name, category="minted_name")
    return redirect(url_for("dashboard.tokens_list"), code=HTTPStatus.SEE_OTHER)


@bp.post("/admin/tokens/<int:token_id>/revoke")
def tokens_revoke(token_id: int) -> WerkzeugResponse:
    _auth_service().revoke(ApiTokenId(token_id))
    return redirect(url_for("dashboard.tokens_list"), code=HTTPStatus.SEE_OTHER)
