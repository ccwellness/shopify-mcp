"""OrderGroove subscription provider — adapter over the REST client.

Normalizes raw OG records into domain `SubscriptionContract` rows. The
mapping was derived from a live probe against lubelife on 2026-05-13 —
see the project memory `project_ordergroove_api.md` for the response
shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import (
    SubscriptionProvider as SubscriptionProviderEnum,
)
from app.domain.enums import (
    SubscriptionStatus,
)
from app.domain.models import (
    StoreId,
    SubscriptionContract,
    SubscriptionContractId,
)
from app.integrations.ordergroove.client import OrderGrooveClient
from app.services.subscriptions.base import CustomerLookup

# OG's `every_period` is an integer code. Inferred mapping (verify if odd
# values surface in production logs).
_PERIOD_CODE_TO_INTERVAL: dict[int, str] = {
    1: "day",
    2: "week",
    3: "month",
    4: "year",
}


class OrderGrooveProvider:
    """Per-store OrderGroove subscription adapter."""

    def __init__(
        self,
        *,
        client: OrderGrooveClient,
        store_id: StoreId,
        customer_lookup: CustomerLookup,
    ) -> None:
        self._client = client
        self._store_id = store_id
        self._customer_lookup = customer_lookup

    def iter_active(self) -> Iterator[SubscriptionContract]:
        for record in self._client.iter_subscriptions():
            yield _normalize(
                record,
                store_id=self._store_id,
                customer_lookup=self._customer_lookup,
            )


def _normalize(
    record: dict[str, Any],
    *,
    store_id: StoreId,
    customer_lookup: CustomerLookup,
) -> SubscriptionContract:
    public_id = str(record["public_id"])
    external_id = record.get("external_id")
    extra = record.get("extra_data") or {}
    gid = (
        external_id
        if isinstance(external_id, str) and external_id
        else extra.get("shopify_contract_id")
    )
    if isinstance(gid, str) and not gid:
        gid = None

    customer_ref = record.get("customer")
    customer_id = customer_lookup(str(customer_ref)) if customer_ref not in (None, "") else None

    every = record.get("every")
    every_period = record.get("every_period")
    interval = (
        _PERIOD_CODE_TO_INTERVAL.get(int(every_period)) if isinstance(every_period, int) else None
    )

    return SubscriptionContract(
        id=SubscriptionContractId(0),  # repo assigns
        store_id=store_id,
        customer_id=customer_id,
        provider=SubscriptionProviderEnum.ORDERGROOVE,
        provider_contract_id=public_id,
        gid=gid if isinstance(gid, str) else None,
        legacy_id=_legacy_id_from_gid(gid) if isinstance(gid, str) else None,
        status=_status_from(record),
        next_billing_date=None,  # OG doesn't expose a single "next" date on this endpoint
        frequency_interval=interval,
        frequency_count=int(every) if isinstance(every, int) else None,
        currency_code=record.get("currency_code"),
        created_at=_parse_dt(record.get("created")),
        updated_at=_parse_dt(record.get("updated")),
    )


def _status_from(record: dict[str, Any]) -> SubscriptionStatus:
    """Map OG's (live, cancelled) shape onto our SubscriptionStatus enum.

    OG semantics observed in the probe:
      live=True,  cancelled=null → ACTIVE
      live=True,  cancelled=...  → CANCELLED (the `cancelled` timestamp wins)
      live=False, cancelled=null → PAUSED (best guess — needs verification
                                  when we encounter one in prod data)
      live=False, cancelled=...  → CANCELLED
    """
    cancelled = record.get("cancelled")
    if cancelled:
        return SubscriptionStatus.CANCELLED
    if record.get("live") is False:
        return SubscriptionStatus.PAUSED
    return SubscriptionStatus.ACTIVE


def _parse_dt(value: object) -> datetime:
    """OG returns timestamps like `"2026-05-11 14:07:35"` (naive UTC).

    We assume UTC because the response carries no offset and the OG docs
    don't promise local time. Fall back to `datetime.now(UTC)` on parse
    failure rather than dropping the record.
    """
    if isinstance(value, str) and value:
        s = value.replace(" ", "T")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return datetime.now(tz=UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    return datetime.now(tz=UTC)


def _legacy_id_from_gid(gid: str) -> int | None:
    """Pull the numeric suffix out of a `gid://shopify/.../<id>` string."""
    if "/" not in gid:
        return None
    tail = gid.rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return None
