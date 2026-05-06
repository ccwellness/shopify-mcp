"""GET /api/v1/orders — paginated cross-store order listing (TR-32).

Query params (all optional):

  store_id            repeatable; e.g. ?store_id=1&store_id=2
  since, until        ISO 8601 datetime
  financial_status    one of FinancialStatus's values
  fulfillment_status  one of FulfillmentStatus's values
  sku                 matches when any line item has the given SKU
  customer_id         int
  customer_email      string
  min_total           Decimal string
  limit               1..200 (clamped server-side)
  cursor              opaque page token from a prior response

Response:
  { "items": [...], "next_cursor": str | null, "limit": int }

Auth: routes are open in v1 alpha; bearer-token gating lands in the
next slice (TR-4).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request
from flask.wrappers import Response

from app.blueprints.api.v1._serialize import order_to_json
from app.domain.enums import FinancialStatus, FulfillmentStatus
from app.domain.models import CustomerId, OrderId, StoreId
from app.domain.specs import OrderSpec
from app.services.order_query import DEFAULT_LIMIT, OrderQueryService

bp = Blueprint("orders", __name__, url_prefix="/orders")


class _BadRequest(Exception):
    """Internal — caught by the route to return 400 with a helpful message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _service() -> OrderQueryService:
    svc = current_app.extensions.get("order_query_service")
    if svc is None:
        raise RuntimeError("order_query_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _parse_int(field: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise _BadRequest(f"{field} must be an integer (got {raw!r})") from exc


def _parse_datetime(field: str, raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise _BadRequest(f"{field} must be ISO 8601 datetime (got {raw!r})") from exc


def _parse_decimal(field: str, raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise _BadRequest(f"{field} must be a decimal number (got {raw!r})") from exc


def _parse_enum[E: (FinancialStatus, FulfillmentStatus)](
    field: str, raw: str, enum_cls: type[E]
) -> E:
    try:
        return enum_cls(raw)
    except ValueError as exc:
        valid = ", ".join(member.value for member in enum_cls)
        raise _BadRequest(f"{field} must be one of [{valid}] (got {raw!r})") from exc


def _parse_spec_and_paging(args: dict[str, list[str]]) -> tuple[OrderSpec, int, str | None]:
    """Build an OrderSpec + (limit, cursor) from a request.args MultiDict-as-dict."""
    store_ids: tuple[StoreId, ...] | None = None
    raw_store_ids = args.get("store_id") or []
    if raw_store_ids:
        store_ids = tuple(StoreId(_parse_int("store_id", v)) for v in raw_store_ids)

    def _first(name: str) -> str | None:
        values = args.get(name)
        return values[0] if values else None

    since_raw = _first("since")
    until_raw = _first("until")
    fs_raw = _first("financial_status")
    fls_raw = _first("fulfillment_status")
    sku = _first("sku")
    cust_id_raw = _first("customer_id")
    cust_email = _first("customer_email")
    min_total_raw = _first("min_total")
    tag = _first("tag")
    limit_raw = _first("limit")
    cursor = _first("cursor")

    spec = OrderSpec(
        store_ids=store_ids,
        since=_parse_datetime("since", since_raw) if since_raw else None,
        until=_parse_datetime("until", until_raw) if until_raw else None,
        financial_status=(
            _parse_enum("financial_status", fs_raw, FinancialStatus) if fs_raw else None
        ),
        fulfillment_status=(
            _parse_enum("fulfillment_status", fls_raw, FulfillmentStatus) if fls_raw else None
        ),
        sku=sku,
        customer_id=(CustomerId(_parse_int("customer_id", cust_id_raw)) if cust_id_raw else None),
        customer_email=cust_email,
        min_total=_parse_decimal("min_total", min_total_raw) if min_total_raw else None,
        tag=tag,
    )
    limit = _parse_int("limit", limit_raw) if limit_raw else DEFAULT_LIMIT
    return spec, limit, cursor


def _error(message: str, status: HTTPStatus) -> tuple[Response, int]:
    return jsonify({"error": message}), int(status)


@bp.errorhandler(_BadRequest)
def _handle_bad_request(exc: _BadRequest) -> tuple[Response, int]:
    return _error(exc.message, HTTPStatus.BAD_REQUEST)


@bp.get("")
def list_orders() -> tuple[Response, int]:
    args = {k: request.args.getlist(k) for k in request.args}
    spec, limit, cursor = _parse_spec_and_paging(args)
    page = _service().list_orders(spec, limit=limit, cursor=cursor)
    body = {
        "items": [order_to_json(o) for o in page.items],
        "next_cursor": page.next_cursor,
        "limit": min(max(1, limit), 200),
    }
    return jsonify(body), int(HTTPStatus.OK)


@bp.get("/<int:order_id>")
def get_order(order_id: int) -> tuple[Response, int]:
    order = _service().get_order_by_id(OrderId(order_id))
    if order is None:
        return _error(f"order {order_id} not found", HTTPStatus.NOT_FOUND)
    return jsonify(order_to_json(order)), int(HTTPStatus.OK)


# Imported for re-export so route handlers can be wired through factories
# without exposing them above this line.
_: tuple[Callable[..., object], ...] = (list_orders, get_order)
