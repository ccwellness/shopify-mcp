"""GET /api/v1/analytics/daily — per (store, day) KPI rows (TR-31).

Query params:

  since        YYYY-MM-DD, required (inclusive lower bound).
  until        YYYY-MM-DD, required (inclusive upper bound).
  store_id     repeatable, optional. Cross-store sweep when omitted.

Returns AnalyticsKpiDay rows the `AnalyticsService.compute_kpi_day` job
already persisted; this endpoint never triggers a recompute. Operators
who need fresh numbers run `flask analytics compute` first.
"""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request
from flask.wrappers import Response

from app.blueprints.api.v1._params import BadRequestError, first, parse_date, parse_int
from app.blueprints.api.v1._serialize import kpi_day_to_json
from app.domain.models import StoreId
from app.services.analytics import AnalyticsService

bp = Blueprint("analytics", __name__, url_prefix="/analytics")


def _service() -> AnalyticsService:
    svc = current_app.extensions.get("analytics_service")
    if svc is None:
        raise RuntimeError("analytics_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _error(message: str, status: HTTPStatus) -> tuple[Response, int]:
    return jsonify({"error": message}), int(status)


@bp.errorhandler(BadRequestError)
def _handle_bad_request(exc: BadRequestError) -> tuple[Response, int]:
    return _error(exc.message, HTTPStatus.BAD_REQUEST)


@bp.get("/daily")
def daily() -> tuple[Response, int]:
    args = {k: request.args.getlist(k) for k in request.args}

    since_raw = first(args, "since")
    until_raw = first(args, "until")
    if since_raw is None or until_raw is None:
        return _error("since and until are required YYYY-MM-DD dates", HTTPStatus.BAD_REQUEST)
    since = parse_date("since", since_raw)
    until = parse_date("until", until_raw)

    raw_store_ids = args.get("store_id") or []
    store_ids: tuple[StoreId, ...] | None = (
        tuple(StoreId(parse_int("store_id", v)) for v in raw_store_ids) if raw_store_ids else None
    )

    try:
        rows = _service().list_kpis(store_ids=store_ids, since=since, until=until)
    except ValueError as exc:
        return _error(str(exc), HTTPStatus.BAD_REQUEST)

    body = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "items": [kpi_day_to_json(r) for r in rows],
    }
    return jsonify(body), int(HTTPStatus.OK)


_: tuple[Callable[..., object], ...] = (daily,)
