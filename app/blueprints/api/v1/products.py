"""GET /api/v1/products + /api/v1/products/<id>.

List endpoint mirrors orders: store_id (repeatable), status, vendor,
product_type, title_query, tag, limit, cursor.

Detail endpoint bundles everything the dashboard's `/products/<id>`
page needs into a single JSON blob: product, inventory_levels,
sales_series, recent_orders. Sales window defaults to trailing 30 days
and is configurable via ?since=&until= (ISO 8601 datetime, same parsing
as /orders).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request
from flask.wrappers import Response

from app.blueprints.api.v1._params import (
    BadRequestError,
    first,
    parse_datetime,
    parse_enum,
    parse_int,
)
from app.blueprints.api.v1._serialize import (
    inventory_level_to_json,
    product_order_summary_to_json,
    product_sales_day_to_json,
    product_to_json,
)
from app.domain.enums import ProductStatus
from app.domain.models import ProductId, StoreId
from app.domain.specs import ProductSpec
from app.services.product_query import DEFAULT_LIMIT, ProductQueryService

bp = Blueprint("products", __name__, url_prefix="/products")

_DEFAULT_SALES_WINDOW_DAYS = 30


def _service() -> ProductQueryService:
    svc = current_app.extensions.get("product_query_service")
    if svc is None:
        raise RuntimeError("product_query_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _parse_list_spec(args: dict[str, list[str]]) -> tuple[ProductSpec, int, str | None]:
    store_ids: tuple[StoreId, ...] | None = None
    raw_store_ids = args.get("store_id") or []
    if raw_store_ids:
        store_ids = tuple(StoreId(parse_int("store_id", v)) for v in raw_store_ids)

    status_raw = first(args, "status")
    vendor = first(args, "vendor")
    product_type = first(args, "product_type")
    title_query = first(args, "title_query")
    tag = first(args, "tag")
    limit_raw = first(args, "limit")
    cursor = first(args, "cursor")

    spec = ProductSpec(
        store_ids=store_ids,
        status=parse_enum("status", status_raw, ProductStatus) if status_raw else None,
        title_query=title_query,
        vendor=vendor,
        product_type=product_type,
        tag=tag,
    )
    limit = parse_int("limit", limit_raw) if limit_raw else DEFAULT_LIMIT
    return spec, limit, cursor


def _error(message: str, status: HTTPStatus) -> tuple[Response, int]:
    return jsonify({"error": message}), int(status)


@bp.errorhandler(BadRequestError)
def _handle_bad_request(exc: BadRequestError) -> tuple[Response, int]:
    return _error(exc.message, HTTPStatus.BAD_REQUEST)


@bp.get("")
def list_products() -> tuple[Response, int]:
    args = {k: request.args.getlist(k) for k in request.args}
    spec, limit, cursor = _parse_list_spec(args)
    page = _service().list_products(spec, limit=limit, cursor=cursor)
    body = {
        "items": [product_to_json(p) for p in page.items],
        "next_cursor": page.next_cursor,
        "limit": min(max(1, limit), 200),
    }
    return jsonify(body), int(HTTPStatus.OK)


@bp.get("/<int:product_id>")
def get_product(product_id: int) -> tuple[Response, int]:
    svc = _service()
    product = svc.get_product_by_id(ProductId(product_id))
    if product is None:
        return _error(f"product {product_id} not found", HTTPStatus.NOT_FOUND)

    # Window: parse user input first; default trailing 30d in UTC, [since, until).
    args = {k: request.args.getlist(k) for k in request.args}
    since_raw = first(args, "since")
    until_raw = first(args, "until")
    now = datetime.now(tz=UTC)
    until = parse_datetime("until", until_raw) if until_raw else now
    since = (
        parse_datetime("since", since_raw)
        if since_raw
        else until - timedelta(days=_DEFAULT_SALES_WINDOW_DAYS)
    )

    variant_ids = tuple(v.id for v in product.variants)
    inventory_levels = svc.get_inventory_for_variants(product.store_id, variant_ids)
    sales_series = svc.get_sales_by_day(product.store_id, ProductId(product_id), since, until)
    recent_orders = svc.get_recent_orders(product.store_id, ProductId(product_id))

    body = {
        "product": product_to_json(product),
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "inventory_levels": [inventory_level_to_json(lv) for lv in inventory_levels],
        "sales_series": [product_sales_day_to_json(d) for d in sales_series],
        "recent_orders": [product_order_summary_to_json(o) for o in recent_orders],
    }
    return jsonify(body), int(HTTPStatus.OK)


_: tuple[Callable[..., object], ...] = (list_products, get_product)
