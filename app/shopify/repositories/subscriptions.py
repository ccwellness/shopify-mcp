"""Live SubscriptionRepository — reads subscriptions from OrderGroove REST.

Scope: OrderGroove stores. Native Shopify `subscriptionContracts` are not
materialized live (the same gap the sync path has — only the OrderGroove
provider is implemented today), so non-OG stores contribute no rows. Raw OG
access is also available via the `ordergroove_live_*` MCP tools.

OrderGroove normalization is inlined here (rather than imported from
`app.services.subscriptions.ordergroove`) so `app.shopify` stays an isolated
adapter that never imports the services layer — the OrderGroove REST *client*
in `app.integrations` is fair game, the service-layer provider is not.

Synthetic contract id: OG records have no local integer PK. We use the
gid-derived `legacy_id` when present, else a stable CRC32 of the OG `public_id`,
so `get_by_id` can round-trip within a process.
"""

from __future__ import annotations

import base64
import zlib
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import SubscriptionProvider, SubscriptionStatus
from app.domain.models import (
    CustomerId,
    Page,
    StoreId,
    SubscriptionContract,
    SubscriptionContractId,
)
from app.domain.specs import SubscriptionSpec
from app.integrations.ordergroove.client import OrderGrooveClient
from app.shopify.live_paging import gid_tail
from app.shopify.repositories.store_index import StoreIndex

# OG's `every_period` is an integer code (mapping per project memory).
_PERIOD_CODE_TO_INTERVAL: dict[int, str] = {1: "day", 2: "week", 3: "month", 4: "year"}


def _encode_offset(offset: int) -> str:
    return base64.urlsafe_b64encode(f"off|{offset}".encode()).decode("ascii").rstrip("=")


def _decode_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    pad = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(cursor + pad).decode()
    _, _, n = raw.partition("|")
    return int(n) if n.isdigit() else 0


def _parse_dt(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            return datetime.now(tz=UTC)
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return datetime.now(tz=UTC)


def _status_from(record: dict[str, Any]) -> SubscriptionStatus:
    if record.get("cancelled"):
        return SubscriptionStatus.CANCELLED
    if record.get("live") is False:
        return SubscriptionStatus.PAUSED
    return SubscriptionStatus.ACTIVE


def _normalize(record: dict[str, Any], *, store_id: StoreId) -> SubscriptionContract:
    public_id = str(record["public_id"])
    external_id = record.get("external_id")
    extra = record.get("extra_data") or {}
    gid = (
        external_id
        if isinstance(external_id, str) and external_id
        else extra.get("shopify_contract_id")
    )
    gid = gid if isinstance(gid, str) and gid else None

    customer_ref = record.get("customer")
    customer_id = (
        CustomerId(int(customer_ref))
        if isinstance(customer_ref, (str, int)) and str(customer_ref).isdigit()
        else None
    )

    every = record.get("every")
    every_period = record.get("every_period")
    interval = (
        _PERIOD_CODE_TO_INTERVAL.get(int(every_period)) if isinstance(every_period, int) else None
    )

    legacy_id = gid_tail(gid) if gid else None
    synthetic = legacy_id if legacy_id is not None else zlib.crc32(public_id.encode())

    return SubscriptionContract(
        id=SubscriptionContractId(synthetic),
        store_id=store_id,
        customer_id=customer_id,
        provider=SubscriptionProvider.ORDERGROOVE,
        provider_contract_id=public_id,
        gid=gid,
        legacy_id=legacy_id,
        status=_status_from(record),
        next_billing_date=None,
        frequency_interval=interval,
        frequency_count=int(every) if isinstance(every, int) else None,
        currency_code=record.get("currency_code"),
        created_at=_parse_dt(record.get("created")),
        updated_at=_parse_dt(record.get("updated")),
    )


class LiveSubscriptionRepository:
    def __init__(self, index: StoreIndex) -> None:
        self._index = index

    def _contracts_for_store(self, store_id: StoreId) -> list[SubscriptionContract]:
        cfg = self._index.config_for_id(store_id)
        if cfg is None or not cfg.ordergroove_api_key:
            return []
        client = OrderGrooveClient(cfg.ordergroove_api_key)
        return [_normalize(record, store_id=store_id) for record in client.iter_subscriptions()]

    def _all_matching(self, spec: SubscriptionSpec) -> list[SubscriptionContract]:
        store_ids = (
            self._index.all_store_ids()
            if spec.store_ids is None
            else tuple(sid for sid in spec.store_ids if self._index.key_for(sid) is not None)
        )
        contracts: list[SubscriptionContract] = []
        for store_id in store_ids:
            for c in self._contracts_for_store(store_id):
                if spec.status is not None and c.status != spec.status:
                    continue
                if spec.provider is not None and c.provider != spec.provider:
                    continue
                if spec.customer_id is not None and c.customer_id != spec.customer_id:
                    continue
                contracts.append(c)
        contracts.sort(key=lambda c: c.updated_at, reverse=True)
        return contracts

    def find(
        self, spec: SubscriptionSpec, *, limit: int = 50, cursor: str | None = None
    ) -> Page[SubscriptionContract]:
        contracts = self._all_matching(spec)
        offset = _decode_offset(cursor)
        window = contracts[offset : offset + limit]
        next_cursor = _encode_offset(offset + limit) if offset + limit < len(contracts) else None
        return Page(items=tuple(window), next_cursor=next_cursor)

    def get(self, contract_id: SubscriptionContractId) -> SubscriptionContract | None:
        for store_id in self._index.all_store_ids():
            for c in self._contracts_for_store(store_id):
                if c.id == contract_id:
                    return c
        return None

    def upsert(self, contract: SubscriptionContract) -> None:  # noqa: ARG002
        return None
