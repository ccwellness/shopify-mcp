"""In-memory implementations of every Repository Protocol.

Each fake is backed by a `dict` keyed by primary id (and secondary
indexes for gid / store_key lookups). They store domain dataclasses
verbatim — no copy on insert, no copy on read — so the test author
sees exactly what they put in.

Pagination cursors are simple integer offsets encoded as strings; tests
that exercise cursoring pages can assert on the round trip without
caring about the encoding.
"""

from __future__ import annotations

import gzip
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import count

from app.domain.enums import FinancialStatus, SyncResource, WebhookProcessingStatus
from app.domain.models import (
    AnalyticsKpiDay,
    ApiAuditLogEntry,
    ApiAuditLogId,
    ApiToken,
    ApiTokenId,
    Customer,
    CustomerId,
    InventoryItem,
    InventoryItemId,
    InventoryLevel,
    InventoryLevelId,
    Location,
    LocationId,
    Order,
    OrderId,
    Page,
    Product,
    ProductId,
    SessionsDay,
    Store,
    StoreId,
    SubscriptionContract,
    SubscriptionContractId,
    SyncStateRow,
    VariantId,
)
from app.domain.specs import (
    AnalyticsWindowSpec,
    InventorySpec,
    OrderSpec,
    ProductSpec,
    SubscriptionSpec,
)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------


@dataclass
class _WebhookEventState:
    id: int
    store_id: StoreId
    topic: str
    shopify_webhook_id: str | None
    received_at: datetime
    hmac_valid: bool
    raw_body: bytes
    processing_status: WebhookProcessingStatus = WebhookProcessingStatus.RECEIVED
    error: str | None = None


@dataclass
class InMemoryDatabase:
    """Shared mutable state across all in-memory repositories.

    Tests usually instantiate this once per test (or per fixture scope)
    and pass it to `InMemoryUnitOfWork(db)`.
    """

    stores: dict[StoreId, Store] = field(default_factory=dict)
    locations: dict[LocationId, Location] = field(default_factory=dict)
    customers: dict[CustomerId, Customer] = field(default_factory=dict)
    products: dict[ProductId, Product] = field(default_factory=dict)
    inventory_items: dict[InventoryItemId, InventoryItem] = field(default_factory=dict)
    inventory_levels: dict[InventoryLevelId, InventoryLevel] = field(default_factory=dict)
    orders: dict[OrderId, Order] = field(default_factory=dict)
    subscriptions: dict[SubscriptionContractId, SubscriptionContract] = field(default_factory=dict)
    sessions_day: dict[tuple[StoreId, date], SessionsDay] = field(default_factory=dict)
    kpi_day: dict[tuple[StoreId, date], AnalyticsKpiDay] = field(default_factory=dict)
    sync_state: dict[tuple[StoreId, SyncResource], SyncStateRow] = field(default_factory=dict)
    webhook_events: dict[int, _WebhookEventState] = field(default_factory=dict)
    api_tokens: dict[ApiTokenId, ApiToken] = field(default_factory=dict)
    api_audit_log: list[ApiAuditLogEntry] = field(default_factory=list)
    _webhook_id_seq: count[int] = field(default_factory=lambda: count(1))
    _api_token_id_seq: count[int] = field(default_factory=lambda: count(1))
    _audit_log_id_seq: count[int] = field(default_factory=lambda: count(1))


def _store_match(store_id: StoreId, allowed: tuple[StoreId, ...] | None) -> bool:
    return allowed is None or store_id in allowed


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    return int(cursor)


def _next_cursor(offset: int, total: int) -> str | None:
    return str(offset) if offset < total else None


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


class InMemoryStoreRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def list_active(self) -> tuple[Store, ...]:
        return tuple(s for s in self._db.stores.values() if s.active)

    def get(self, store_id: StoreId) -> Store | None:
        return self._db.stores.get(store_id)

    def get_by_key(self, store_key: str) -> Store | None:
        for s in self._db.stores.values():
            if s.store_key == store_key:
                return s
        return None

    def get_by_domain(self, shop_domain: str) -> Store | None:
        for s in self._db.stores.values():
            if s.shop_domain == shop_domain:
                return s
        return None

    def upsert(self, store: Store) -> None:
        self._db.stores[store.id] = store


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


class InMemoryLocationRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def list_for_store(self, store_id: StoreId) -> tuple[Location, ...]:
        return tuple(loc for loc in self._db.locations.values() if loc.store_id == store_id)

    def get(self, location_id: LocationId) -> Location | None:
        return self._db.locations.get(location_id)

    def get_by_gid(self, store_id: StoreId, gid: str) -> Location | None:
        for loc in self._db.locations.values():
            if loc.store_id == store_id and loc.gid == gid:
                return loc
        return None

    def upsert(self, location: Location) -> None:
        self._db.locations[location.id] = location


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


class InMemoryCustomerRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def get(self, customer_id: CustomerId) -> Customer | None:
        return self._db.customers.get(customer_id)

    def get_by_gid(self, store_id: StoreId, gid: str) -> Customer | None:
        for c in self._db.customers.values():
            if c.store_id == store_id and c.gid == gid:
                return c
        return None

    def get_by_email(self, store_id: StoreId, email: str) -> Customer | None:
        for c in self._db.customers.values():
            if c.store_id == store_id and c.email == email:
                return c
        return None

    def upsert(self, customer: Customer) -> None:
        self._db.customers[customer.id] = customer


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def _matches_order(order: Order, spec: OrderSpec) -> bool:
    return (
        _store_match(order.store_id, spec.store_ids)
        and (spec.since is None or order.processed_at >= spec.since)
        and (spec.until is None or order.processed_at <= spec.until)
        and (spec.financial_status is None or order.financial_status == spec.financial_status)
        and (spec.fulfillment_status is None or order.fulfillment_status == spec.fulfillment_status)
        and (spec.customer_id is None or order.customer_id == spec.customer_id)
        and (spec.customer_email is None or order.email == spec.customer_email)
        and (spec.min_total is None or order.total_price >= spec.min_total)
        and (spec.sku is None or any(li.sku == spec.sku for li in order.line_items))
    )


class InMemoryOrderRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def get(self, order_id: OrderId) -> Order | None:
        return self._db.orders.get(order_id)

    def get_by_gid(self, store_id: StoreId, gid: str) -> Order | None:
        for o in self._db.orders.values():
            if o.store_id == store_id and o.gid == gid:
                return o
        return None

    def find(
        self,
        spec: OrderSpec,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[Order]:
        ordered = sorted(
            (o for o in self._db.orders.values() if _matches_order(o, spec)),
            key=lambda o: (o.processed_at, o.id),
            reverse=True,
        )
        offset = _decode_cursor(cursor)
        page = tuple(ordered[offset : offset + limit])
        new_offset = offset + len(page)
        return Page(items=page, next_cursor=_next_cursor(new_offset, len(ordered)))

    def count_by_status(
        self,
        store_id: StoreId,
        since: datetime,
        until: datetime,
    ) -> dict[FinancialStatus, int]:
        out: dict[FinancialStatus, int] = defaultdict(int)
        for o in self._db.orders.values():
            if o.store_id != store_id:
                continue
            if o.processed_at < since or o.processed_at > until:
                continue
            if o.financial_status is None:
                continue
            out[o.financial_status] += 1
        return dict(out)

    def upsert(self, order: Order) -> None:
        self._db.orders[order.id] = order


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def _matches_product(product: Product, spec: ProductSpec) -> bool:
    return (
        _store_match(product.store_id, spec.store_ids)
        and (spec.status is None or product.status == spec.status)
        and (spec.title_query is None or spec.title_query.lower() in product.title.lower())
        and (spec.handle is None or product.handle == spec.handle)
        and (spec.vendor is None or product.vendor == spec.vendor)
        and (spec.product_type is None or product.product_type == spec.product_type)
        and (spec.tag is None or spec.tag in product.tags)
    )


class InMemoryProductRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def get(self, product_id: ProductId) -> Product | None:
        return self._db.products.get(product_id)

    def get_by_gid(self, store_id: StoreId, gid: str) -> Product | None:
        for p in self._db.products.values():
            if p.store_id == store_id and p.gid == gid:
                return p
        return None

    def get_by_handle(self, store_id: StoreId, handle: str) -> Product | None:
        for p in self._db.products.values():
            if p.store_id == store_id and p.handle == handle:
                return p
        return None

    def find(
        self,
        spec: ProductSpec,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[Product]:
        ordered = sorted(
            (p for p in self._db.products.values() if _matches_product(p, spec)),
            key=lambda p: (p.title.lower(), p.id),
        )
        offset = _decode_cursor(cursor)
        page = tuple(ordered[offset : offset + limit])
        new_offset = offset + len(page)
        return Page(items=page, next_cursor=_next_cursor(new_offset, len(ordered)))

    def variant_gid_map(self, store_id: StoreId) -> dict[str, VariantId]:
        out: dict[str, VariantId] = {}
        for p in self._db.products.values():
            if p.store_id != store_id:
                continue
            for v in p.variants:
                out[v.gid] = v.id
        return out

    def product_gid_map(self, store_id: StoreId) -> dict[str, ProductId]:
        return {p.gid: p.id for p in self._db.products.values() if p.store_id == store_id}

    def upsert(self, product: Product) -> None:
        self._db.products[product.id] = product


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def _matches_inventory_level(level: InventoryLevel, spec: InventorySpec) -> bool:
    if not _store_match(level.store_id, spec.store_ids):
        return False
    if spec.location_id is not None and level.location_id != spec.location_id:
        return False
    if spec.low_stock_threshold is not None:
        if level.available is None or level.available >= spec.low_stock_threshold:
            return False
    return True


class InMemoryInventoryRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def list_levels(
        self,
        spec: InventorySpec,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[InventoryLevel]:
        levels = list(self._db.inventory_levels.values())
        if spec.sku is not None:
            allowed_items = {
                item.id for item in self._db.inventory_items.values() if item.sku == spec.sku
            }
            levels = [lv for lv in levels if lv.inventory_item_id in allowed_items]
        ordered = sorted(
            (lv for lv in levels if _matches_inventory_level(lv, spec)),
            key=lambda lv: lv.id,
        )
        offset = _decode_cursor(cursor)
        page = tuple(ordered[offset : offset + limit])
        new_offset = offset + len(page)
        return Page(items=page, next_cursor=_next_cursor(new_offset, len(ordered)))

    def get_item(self, store_id: StoreId, gid: str) -> InventoryItem | None:
        for item in self._db.inventory_items.values():
            if item.store_id == store_id and item.gid == gid:
                return item
        return None

    def list_low_stock(
        self,
        store_id: StoreId,
        threshold: int,
        *,
        limit: int = 50,
    ) -> tuple[InventoryLevel, ...]:
        candidates = [
            lv
            for lv in self._db.inventory_levels.values()
            if lv.store_id == store_id and lv.available is not None and lv.available < threshold
        ]
        candidates.sort(key=lambda lv: lv.available or 0)
        return tuple(candidates[:limit])

    def upsert_item(self, item: InventoryItem) -> None:
        self._db.inventory_items[item.id] = item

    def upsert_level(self, level: InventoryLevel) -> None:
        self._db.inventory_levels[level.id] = level


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


def _matches_subscription(
    contract: SubscriptionContract,
    spec: SubscriptionSpec,
) -> bool:
    if not _store_match(contract.store_id, spec.store_ids):
        return False
    if spec.customer_id is not None and contract.customer_id != spec.customer_id:
        return False
    if spec.status is not None and contract.status != spec.status:
        return False
    if spec.provider is not None and contract.provider != spec.provider:
        return False
    return True


class InMemorySubscriptionRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def get(self, contract_id: SubscriptionContractId) -> SubscriptionContract | None:
        return self._db.subscriptions.get(contract_id)

    def find(
        self,
        spec: SubscriptionSpec,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[SubscriptionContract]:
        ordered = sorted(
            (c for c in self._db.subscriptions.values() if _matches_subscription(c, spec)),
            key=lambda c: c.id,
        )
        offset = _decode_cursor(cursor)
        page = tuple(ordered[offset : offset + limit])
        new_offset = offset + len(page)
        return Page(items=page, next_cursor=_next_cursor(new_offset, len(ordered)))

    def upsert(self, contract: SubscriptionContract) -> None:
        self._db.subscriptions[contract.id] = contract


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


class InMemoryAnalyticsRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def get_sessions_day(self, store_id: StoreId, day: date) -> SessionsDay | None:
        return self._db.sessions_day.get((store_id, day))

    def upsert_sessions_day(self, row: SessionsDay) -> None:
        self._db.sessions_day[(row.store_id, row.date)] = row

    def list_sessions(self, spec: AnalyticsWindowSpec) -> tuple[SessionsDay, ...]:
        return tuple(
            sorted(
                (
                    row
                    for row in self._db.sessions_day.values()
                    if _store_match(row.store_id, spec.store_ids)
                    and spec.since <= row.date <= spec.until
                ),
                key=lambda r: (r.store_id, r.date),
            )
        )

    def get_kpi_day(self, store_id: StoreId, day: date) -> AnalyticsKpiDay | None:
        return self._db.kpi_day.get((store_id, day))

    def upsert_kpi_day(self, row: AnalyticsKpiDay) -> None:
        self._db.kpi_day[(row.store_id, row.date)] = row

    def list_kpis(self, spec: AnalyticsWindowSpec) -> tuple[AnalyticsKpiDay, ...]:
        return tuple(
            sorted(
                (
                    row
                    for row in self._db.kpi_day.values()
                    if _store_match(row.store_id, spec.store_ids)
                    and spec.since <= row.date <= spec.until
                ),
                key=lambda r: (r.store_id, r.date),
            )
        )


# ---------------------------------------------------------------------------
# Sync state
# ---------------------------------------------------------------------------


class InMemorySyncStateRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def get(self, store_id: StoreId, resource: SyncResource) -> SyncStateRow | None:
        return self._db.sync_state.get((store_id, resource))

    def list_for_store(self, store_id: StoreId) -> tuple[SyncStateRow, ...]:
        return tuple(row for (sid, _), row in self._db.sync_state.items() if sid == store_id)

    def upsert(self, row: SyncStateRow) -> None:
        self._db.sync_state[(row.store_id, row.resource)] = row


# ---------------------------------------------------------------------------
# Webhook event log
# ---------------------------------------------------------------------------


class InMemoryWebhookEventLogRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def record(  # noqa: PLR0913 — kwargs-only by design (matches the Protocol)
        self,
        *,
        store_id: StoreId,
        topic: str,
        shopify_webhook_id: str | None,
        received_at: datetime,
        hmac_valid: bool,
        raw_body: bytes,
    ) -> int:
        event_id = next(self._db._webhook_id_seq)  # noqa: SLF001 — owned shared state
        self._db.webhook_events[event_id] = _WebhookEventState(
            id=event_id,
            store_id=store_id,
            topic=topic,
            shopify_webhook_id=shopify_webhook_id,
            received_at=received_at,
            hmac_valid=hmac_valid,
            raw_body=gzip.compress(raw_body),
        )
        return event_id

    def get_for_processing(self, event_id: int) -> tuple[StoreId, str, bytes] | None:
        row = self._db.webhook_events.get(event_id)
        if row is None or row.processing_status == WebhookProcessingStatus.PROCESSED:
            return None
        return row.store_id, row.topic, gzip.decompress(row.raw_body)

    def mark_processed(self, event_id: int) -> None:
        row = self._db.webhook_events.get(event_id)
        if row is None:
            return
        row.processing_status = WebhookProcessingStatus.PROCESSED
        row.error = None

    def mark_failed(self, event_id: int, error: str) -> None:
        row = self._db.webhook_events.get(event_id)
        if row is None:
            return
        row.processing_status = WebhookProcessingStatus.FAILED
        row.error = error


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


class InMemoryApiTokenRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def get_by_hash(self, token_hash: str) -> ApiToken | None:
        for tok in self._db.api_tokens.values():
            if tok.token_hash == token_hash:
                return tok
        return None

    def list_active(self) -> tuple[ApiToken, ...]:
        return tuple(t for t in self._db.api_tokens.values() if t.revoked_at is None)

    def upsert(self, token: ApiToken) -> ApiTokenId:
        # If a row with this hash exists, replace it. Otherwise assign a new id.
        for existing in self._db.api_tokens.values():
            if existing.token_hash == token.token_hash:
                self._db.api_tokens[existing.id] = ApiToken(
                    id=existing.id,
                    name=token.name,
                    token_hash=token.token_hash,
                    store_id=token.store_id,
                    created_at=existing.created_at,
                    expires_at=token.expires_at,
                    revoked_at=token.revoked_at,
                    last_used_at=token.last_used_at,
                )
                return existing.id
        new_id = ApiTokenId(next(self._db._api_token_id_seq))  # noqa: SLF001
        self._db.api_tokens[new_id] = ApiToken(
            id=new_id,
            name=token.name,
            token_hash=token.token_hash,
            store_id=token.store_id,
            created_at=token.created_at,
            expires_at=token.expires_at,
            revoked_at=token.revoked_at,
            last_used_at=token.last_used_at,
        )
        return new_id

    def touch_last_used(self, token_id: ApiTokenId, when: datetime) -> None:
        existing = self._db.api_tokens.get(token_id)
        if existing is None:
            return
        self._db.api_tokens[token_id] = ApiToken(
            id=existing.id,
            name=existing.name,
            token_hash=existing.token_hash,
            store_id=existing.store_id,
            created_at=existing.created_at,
            expires_at=existing.expires_at,
            revoked_at=existing.revoked_at,
            last_used_at=when,
        )

    def revoke(self, token_id: ApiTokenId, when: datetime) -> None:
        existing = self._db.api_tokens.get(token_id)
        if existing is None:
            return
        self._db.api_tokens[token_id] = ApiToken(
            id=existing.id,
            name=existing.name,
            token_hash=existing.token_hash,
            store_id=existing.store_id,
            created_at=existing.created_at,
            expires_at=existing.expires_at,
            revoked_at=when,
            last_used_at=existing.last_used_at,
        )


# ---------------------------------------------------------------------------
# API audit log
# ---------------------------------------------------------------------------


class InMemoryApiAuditLogRepository:
    def __init__(self, db: InMemoryDatabase) -> None:
        self._db = db

    def record(self, entry: ApiAuditLogEntry) -> None:
        # Assign an id if the caller passed 0 / a placeholder.
        new_id = ApiAuditLogId(next(self._db._audit_log_id_seq))  # noqa: SLF001
        self._db.api_audit_log.append(
            ApiAuditLogEntry(
                id=new_id,
                ts=entry.ts,
                caller_identity=entry.caller_identity,
                store_id=entry.store_id,
                surface=entry.surface,
                route_or_tool=entry.route_or_tool,
                params_sanitized=entry.params_sanitized,
                status_code=entry.status_code,
                latency_ms=entry.latency_ms,
                request_id=entry.request_id,
            )
        )

    def list_recent(self, *, limit: int = 100) -> tuple[ApiAuditLogEntry, ...]:
        return tuple(reversed(self._db.api_audit_log))[:limit]
