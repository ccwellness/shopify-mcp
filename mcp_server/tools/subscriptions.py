"""`list_subscriptions` + `get_subscription` MCP tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import SubscriptionProvider, SubscriptionStatus
from app.domain.models import CustomerId, StoreId, SubscriptionContractId
from app.domain.specs import SubscriptionSpec
from mcp_server.audit import audited
from mcp_server.server import mcp, services

_MAX_LIMIT = 50


class SubscriptionOut(BaseModel):
    id: int
    store_id: int
    customer_id: int | None
    provider: str
    provider_contract_id: str
    gid: str | None
    legacy_id: int | None
    status: str
    next_billing_date: datetime | None
    frequency_interval: str | None
    frequency_count: int | None
    currency_code: str | None
    created_at: datetime
    updated_at: datetime


class SubscriptionPageOut(BaseModel):
    items: list[SubscriptionOut]
    next_cursor: str | None


class GetSubscriptionOut(BaseModel):
    """Wrapper so the tool always returns a dict shape (FastMCP needs that
    when the underlying result can be None — same trick as get_order)."""

    subscription: SubscriptionOut | None


def _to_subscription(c: Any) -> SubscriptionOut:
    return SubscriptionOut(
        id=int(c.id),
        store_id=int(c.store_id),
        customer_id=int(c.customer_id) if c.customer_id is not None else None,
        provider=c.provider.value,
        provider_contract_id=c.provider_contract_id,
        gid=c.gid,
        legacy_id=c.legacy_id,
        status=c.status.value,
        next_billing_date=c.next_billing_date,
        frequency_interval=c.frequency_interval,
        frequency_count=c.frequency_count,
        currency_code=c.currency_code,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@mcp.tool
@audited("list_subscriptions")
def list_subscriptions(  # noqa: PLR0913 — flat filter args mirror REST + GraphQL
    store_id: list[int] | None = Field(  # noqa: B008 — Pydantic Field-as-default is the idiom
        default=None, description="Optional list of numeric store ids."
    ),
    status: str | None = Field(
        default=None,
        description="One of: active, paused, cancelled, expired, unknown.",
    ),
    provider: str | None = Field(
        default=None,
        description="One of: native, ordergroove, unknown.",
    ),
    customer_id: int | None = Field(
        default=None, description="Filter to one customer's subscriptions."
    ),
    limit: int = Field(default=50, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Field(default=None, description="Opaque next_cursor from a prior page."),
) -> SubscriptionPageOut:
    """Paginated cross-store subscription contracts. Mirrors GET /api/v1/subscriptions.

    Sorts by `updated_at` desc. For a per-customer history, pass
    `customer_id`. Use `status='active'` to skip cancelled records.
    """
    spec = SubscriptionSpec(
        store_ids=tuple(StoreId(s) for s in store_id) if store_id else None,
        customer_id=CustomerId(customer_id) if customer_id is not None else None,
        status=SubscriptionStatus(status) if status else None,
        provider=SubscriptionProvider(provider) if provider else None,
    )
    page = services().subscriptions.list_subscriptions(spec, limit=limit, cursor=cursor)
    return SubscriptionPageOut(
        items=[_to_subscription(c) for c in page.items],
        next_cursor=page.next_cursor,
    )


@mcp.tool
@audited("get_subscription")
def get_subscription(
    contract_id: int = Field(description="Numeric DB id of the subscription contract."),
) -> GetSubscriptionOut:
    """Fetch one subscription contract by numeric id. `subscription` is null if not found."""
    c = services().subscriptions.get_by_id(SubscriptionContractId(contract_id))
    return GetSubscriptionOut(subscription=_to_subscription(c) if c is not None else None)
