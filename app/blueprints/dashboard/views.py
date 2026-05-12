"""Dashboard route handlers (TR-32).

Each view parses its query string into a service call and renders a
Jinja2 template. No SQLAlchemy here — services own all persistence
access. Errors (bad date format, inverted window) re-render the same
page with a flash-style error rather than returning JSON.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse

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
from app.domain.enums import FinancialStatus
from app.domain.models import ApiTokenId, LocationId, StoreId
from app.domain.specs import OrderSpec
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
from app.services.store_compare import StoreComparisonService
from app.services.store_query import StoreQueryService


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
