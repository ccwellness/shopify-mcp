"""GET /api/v1/subscriptions — paginated cross-store subscription listing.

Query params (all optional):

  store_id              repeatable; e.g. ?store_id=1&store_id=2
  status                one of SubscriptionStatus's values
  provider              one of SubscriptionProvider's values
  customer_id           int
  limit                 1..200 (clamped server-side)
  cursor                opaque page token from a prior response

Response:
  { "items": [...], "next_cursor": str | null, "limit": int }
"""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request
from flask.wrappers import Response

from app.blueprints.api.v1._params import (
    BadRequestError,
    first,
    parse_enum,
    parse_int,
)
from app.blueprints.api.v1._serialize import subscription_contract_to_json
from app.domain.enums import SubscriptionProvider, SubscriptionStatus
from app.domain.models import CustomerId, StoreId, SubscriptionContractId
from app.domain.specs import SubscriptionSpec
from app.services.subscription_query import DEFAULT_LIMIT, SubscriptionQueryService

bp = Blueprint("subscriptions", __name__, url_prefix="/subscriptions")


def _service() -> SubscriptionQueryService:
    svc = current_app.extensions.get("subscription_query_service")
    if svc is None:
        raise RuntimeError("subscription_query_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _parse_spec_and_paging(
    args: dict[str, list[str]],
) -> tuple[SubscriptionSpec, int, str | None]:
    store_ids: tuple[StoreId, ...] | None = None
    raw_store_ids = args.get("store_id") or []
    if raw_store_ids:
        store_ids = tuple(StoreId(parse_int("store_id", v)) for v in raw_store_ids)

    status_raw = first(args, "status")
    provider_raw = first(args, "provider")
    cust_id_raw = first(args, "customer_id")
    limit_raw = first(args, "limit")
    cursor = first(args, "cursor")

    spec = SubscriptionSpec(
        store_ids=store_ids,
        customer_id=(CustomerId(parse_int("customer_id", cust_id_raw)) if cust_id_raw else None),
        status=(parse_enum("status", status_raw, SubscriptionStatus) if status_raw else None),
        provider=(
            parse_enum("provider", provider_raw, SubscriptionProvider) if provider_raw else None
        ),
    )
    limit = parse_int("limit", limit_raw) if limit_raw else DEFAULT_LIMIT
    return spec, limit, cursor


def _error(message: str, status: HTTPStatus) -> tuple[Response, int]:
    return jsonify({"error": message}), int(status)


@bp.errorhandler(BadRequestError)
def _handle_bad_request(exc: BadRequestError) -> tuple[Response, int]:
    return _error(exc.message, HTTPStatus.BAD_REQUEST)


@bp.get("")
def list_subscriptions() -> tuple[Response, int]:
    args = {k: request.args.getlist(k) for k in request.args}
    spec, limit, cursor = _parse_spec_and_paging(args)
    page = _service().list_subscriptions(spec, limit=limit, cursor=cursor)
    body = {
        "items": [subscription_contract_to_json(c) for c in page.items],
        "next_cursor": page.next_cursor,
        "limit": min(max(1, limit), 200),
    }
    return jsonify(body), int(HTTPStatus.OK)


@bp.get("/<int:contract_id>")
def get_subscription(contract_id: int) -> tuple[Response, int]:
    contract = _service().get_by_id(SubscriptionContractId(contract_id))
    if contract is None:
        return _error(f"subscription {contract_id} not found", HTTPStatus.NOT_FOUND)
    return jsonify(subscription_contract_to_json(contract)), int(HTTPStatus.OK)


_: tuple[Callable[..., object], ...] = (list_subscriptions, get_subscription)
