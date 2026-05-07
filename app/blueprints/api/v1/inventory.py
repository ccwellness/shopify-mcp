"""GET /api/v1/inventory/low-stock — paginated cross-store low-stock report (TR-32).

Query params (all optional except threshold which has a default):

  store_id     repeatable; e.g. ?store_id=1&store_id=2
  threshold    int; default 10. Levels with `available < threshold` are returned.
  location_id  int; restrict to one location.
  sku          string; restrict to one SKU.
  limit        1..200 (clamped server-side).
  cursor       opaque page token from a prior response.

Levels with `available IS NULL` are excluded — we cannot say something is
low if we do not know how much we have.

Response:
  { "items": [...], "next_cursor": str | null, "limit": int, "threshold": int }
"""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request
from flask.wrappers import Response

from app.blueprints.api.v1._params import BadRequestError, first, parse_int
from app.blueprints.api.v1._serialize import inventory_level_to_json
from app.domain.models import LocationId, StoreId
from app.services.inventory_reporting import (
    DEFAULT_LIMIT,
    DEFAULT_LOW_STOCK_THRESHOLD,
    MAX_LIMIT,
    InventoryReportingService,
)

bp = Blueprint("inventory", __name__, url_prefix="/inventory")


def _service() -> InventoryReportingService:
    svc = current_app.extensions.get("inventory_reporting_service")
    if svc is None:
        raise RuntimeError("inventory_reporting_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _error(message: str, status: HTTPStatus) -> tuple[Response, int]:
    return jsonify({"error": message}), int(status)


@bp.errorhandler(BadRequestError)
def _handle_bad_request(exc: BadRequestError) -> tuple[Response, int]:
    return _error(exc.message, HTTPStatus.BAD_REQUEST)


@bp.get("/low-stock")
def low_stock() -> tuple[Response, int]:
    args = {k: request.args.getlist(k) for k in request.args}

    raw_store_ids = args.get("store_id") or []
    store_ids: tuple[StoreId, ...] | None = (
        tuple(StoreId(parse_int("store_id", v)) for v in raw_store_ids) if raw_store_ids else None
    )

    threshold_raw = first(args, "threshold")
    threshold = (
        parse_int("threshold", threshold_raw) if threshold_raw else DEFAULT_LOW_STOCK_THRESHOLD
    )
    if threshold < 0:
        return _error("threshold must be non-negative", HTTPStatus.BAD_REQUEST)

    location_id_raw = first(args, "location_id")
    location_id = LocationId(parse_int("location_id", location_id_raw)) if location_id_raw else None

    sku = first(args, "sku")

    limit_raw = first(args, "limit")
    limit = parse_int("limit", limit_raw) if limit_raw else DEFAULT_LIMIT
    cursor = first(args, "cursor")

    page = _service().list_low_stock(
        store_ids=store_ids,
        threshold=threshold,
        location_id=location_id,
        sku=sku,
        limit=limit,
        cursor=cursor,
    )
    body = {
        "items": [inventory_level_to_json(level) for level in page.items],
        "next_cursor": page.next_cursor,
        "limit": min(max(1, limit), MAX_LIMIT),
        "threshold": threshold,
    }
    return jsonify(body), int(HTTPStatus.OK)


# Re-exported so wiring tools can introspect handlers without poking the module-private name.
_: tuple[Callable[..., object], ...] = (low_stock,)
