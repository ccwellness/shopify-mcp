"""GET /api/v1/compare/orders — cross-store order rollup (TR-32).

Query params:

  since        ISO 8601 datetime, required. Inclusive lower bound on
               `orders.processed_at` and `refunds.created_at`.
  until        ISO 8601 datetime, required. Exclusive upper bound (same
               half-open `[since, until)` semantics the service uses).
  store_id     repeatable, optional. Restrict comparison to specific
               stores; omitted = every active store.

Response:
  {
    "since": str,
    "until": str,
    "currency_warning": bool,
    "rows": [
      {
        "store_id": int,
        "store_key": str,
        "order_count": int,
        "paid_revenue": str,
        "refunds_total": str,
        "net_revenue": str,
        "units_sold": int,
        "currency_code": str | null,
        "status_counts": { "<status>": int, ... }
      },
      ...
    ]
  }
"""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request
from flask.wrappers import Response

from app.blueprints.api.v1._params import (
    BadRequestError,
    first,
    parse_datetime,
    parse_int,
)
from app.blueprints.api.v1._serialize import store_comparison_to_json
from app.domain.models import StoreId
from app.services.store_compare import StoreComparisonService

bp = Blueprint("compare", __name__, url_prefix="/compare")


def _service() -> StoreComparisonService:
    svc = current_app.extensions.get("store_comparison_service")
    if svc is None:
        raise RuntimeError("store_comparison_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _error(message: str, status: HTTPStatus) -> tuple[Response, int]:
    return jsonify({"error": message}), int(status)


@bp.errorhandler(BadRequestError)
def _handle_bad_request(exc: BadRequestError) -> tuple[Response, int]:
    return _error(exc.message, HTTPStatus.BAD_REQUEST)


@bp.get("/orders")
def compare_orders() -> tuple[Response, int]:
    args = {k: request.args.getlist(k) for k in request.args}

    since_raw = first(args, "since")
    until_raw = first(args, "until")
    if since_raw is None or until_raw is None:
        return _error("since and until are required ISO 8601 datetimes", HTTPStatus.BAD_REQUEST)
    since = parse_datetime("since", since_raw)
    until = parse_datetime("until", until_raw)

    raw_store_ids = args.get("store_id") or []
    store_ids: tuple[StoreId, ...] | None = (
        tuple(StoreId(parse_int("store_id", v)) for v in raw_store_ids) if raw_store_ids else None
    )

    try:
        comparison = _service().compare_orders(since=since, until=until, store_ids=store_ids)
    except ValueError as exc:
        return _error(str(exc), HTTPStatus.BAD_REQUEST)

    return jsonify(store_comparison_to_json(comparison)), int(HTTPStatus.OK)


# Re-exported so wiring tools can introspect handlers without poking the module-private name.
_: tuple[Callable[..., object], ...] = (compare_orders,)
