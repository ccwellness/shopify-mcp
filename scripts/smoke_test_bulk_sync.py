"""End-to-end bulk-sync smoke test against live lubelife.

Exercises the full SyncService loop:

  1. `sync_locations(lubelife)`  — paginated GraphQL, regular query.
     Verifies at least one LocationRow lands and a sync_state row is
     written for resource=locations.
  2. `sync_orders(lubelife, since=24h)` — the real bulk path:
     bulkOperationRunQuery -> poll -> JSONL download -> grouper ->
     normalizer -> upsert. Verifies at least one OrderRow lands with
     line items and a sync_state row is written for resource=orders.

A 24-hour window keeps the bulk op small (typically <30s end-to-end)
while still covering recent activity. If the window happens to be
empty, the test still passes structural checks (sync_state row was
upserted, no exception raised) and prints a notice.

The script does NOT delete any rows — locations/orders fetched from
Shopify are real production data that the dev DB legitimately holds.
Upserts are idempotent so re-runs are safe.

Usage:
    DATABASE_URL=postgresql+psycopg://shopify_connector:dev_password\
@localhost:5432/shopify_connector \\
    uv run python scripts/smoke_test_bulk_sync.py
"""

from __future__ import annotations

import pathlib
import sys
import time
from datetime import UTC, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import func, select  # noqa: E402

from app import create_app  # noqa: E402
from app.db.engine import get_session_factory  # noqa: E402
from app.db.orm.location import LocationRow  # noqa: E402
from app.db.orm.order import OrderRow  # noqa: E402
from app.db.orm.store import StoreRow  # noqa: E402
from app.db.orm.sync_state import SyncStateRowOrm  # noqa: E402
from app.shopify.config import load_store_configs  # noqa: E402

STORE_KEY = "lubelife"
ORDERS_SINCE_HOURS = 24


def _check(label: str, cond: bool, detail: str = "") -> None:
    icon = "OK" if cond else "FAIL"
    extra = f" -- {detail}" if detail else ""
    print(f"[{icon}] {label}{extra}")
    if not cond:
        raise SystemExit(1)


def main() -> int:  # noqa: PLR0915 — linear smoke-test sequence; splitting fragments the flow
    configs = load_store_configs()
    if STORE_KEY not in configs:
        print(f"FAIL: {STORE_KEY!r} not in loaded store configs (no real creds in .env?)")
        return 1

    app = create_app()
    svc = app.extensions.get("sync_service")
    if svc is None:
        print("FAIL: sync_service not wired on app.extensions")
        return 1

    factory = get_session_factory()

    # -------------------------------------------------------------------
    # Locations
    # -------------------------------------------------------------------
    print(f"\n=== sync_locations({STORE_KEY}) ===")
    t0 = time.monotonic()
    loc_result = svc.sync_locations(STORE_KEY)
    loc_elapsed = time.monotonic() - t0
    print(f"  upserted={loc_result.upserted}  elapsed={loc_elapsed:.1f}s")
    _check("locations.upserted >= 1", loc_result.upserted >= 1)

    with factory() as s:
        store = s.scalar(select(StoreRow).where(StoreRow.store_key == STORE_KEY))
        _check(f"store row exists for {STORE_KEY!r}", store is not None)
        assert store is not None

        loc_count = s.scalar(
            select(func.count()).select_from(LocationRow).where(LocationRow.store_id == store.id)
        )
        _check(f"locations table has rows for store (got {loc_count})", (loc_count or 0) >= 1)

        sample_loc = s.scalar(select(LocationRow).where(LocationRow.store_id == store.id).limit(1))
        assert sample_loc is not None
        _check(
            f"sample location.gid is a Shopify GID (got {sample_loc.gid!r})",
            sample_loc.gid.startswith("gid://shopify/Location/"),
        )
        _check(
            f"sample location.name is non-empty (got {sample_loc.name!r})",
            bool(sample_loc.name),
        )

        loc_state = s.scalar(
            select(SyncStateRowOrm).where(
                SyncStateRowOrm.store_id == store.id,
                SyncStateRowOrm.resource == "locations",
            )
        )
        _check("sync_state[locations] row written", loc_state is not None)
        assert loc_state is not None
        _check(
            f"sync_state[locations].last_completed_at set (got {loc_state.last_completed_at})",
            loc_state.last_completed_at is not None,
        )

    # -------------------------------------------------------------------
    # Orders (bulk)
    # -------------------------------------------------------------------
    since = datetime.now(tz=UTC) - timedelta(hours=ORDERS_SINCE_HOURS)
    print(f"\n=== sync_orders({STORE_KEY}, since={since.isoformat()}) ===")

    with factory() as s:
        before_count = (
            s.scalar(
                select(func.count()).select_from(OrderRow).where(OrderRow.store_id == store.id)
            )
            or 0
        )
    print(f"  orders before: {before_count}")

    t0 = time.monotonic()
    order_result = svc.sync_orders(STORE_KEY, since=since, max_wait_seconds=600)
    bulk_elapsed = time.monotonic() - t0
    print(f"  upserted={order_result.upserted}  elapsed={bulk_elapsed:.1f}s")

    with factory() as s:
        after_count = (
            s.scalar(
                select(func.count()).select_from(OrderRow).where(OrderRow.store_id == store.id)
            )
            or 0
        )
        order_state = s.scalar(
            select(SyncStateRowOrm).where(
                SyncStateRowOrm.store_id == store.id,
                SyncStateRowOrm.resource == "orders",
            )
        )

    _check("sync_state[orders] row written", order_state is not None)
    assert order_state is not None
    _check(
        f"sync_state[orders].last_completed_at set (got {order_state.last_completed_at})",
        order_state.last_completed_at is not None,
    )

    if order_result.upserted == 0:
        print(
            f"  NOTE: zero orders in last {ORDERS_SINCE_HOURS}h window — "
            f"pipeline ran clean but had no rows to validate row-level."
        )
    else:
        _check(
            f"orders table count grew or held steady (before={before_count}, after={after_count})",
            after_count >= before_count,
        )
        # Inspect one order from the window to confirm normalizer + upsert wired up.
        with factory() as s:
            recent = s.scalar(
                select(OrderRow)
                .where(OrderRow.store_id == store.id, OrderRow.updated_at >= since)
                .order_by(OrderRow.updated_at.desc())
                .limit(1)
            )
            assert recent is not None
            line_items = recent.line_items
            shipping = recent.shipping_address
        _check(
            f"recent order has Shopify GID (got {recent.gid!r})",
            recent.gid.startswith("gid://shopify/Order/"),
        )
        _check(f"recent order.legacy_id set (got {recent.legacy_id})", recent.legacy_id > 0)
        _check(
            f"recent order has currency_code (got {recent.currency_code!r})",
            bool(recent.currency_code),
        )
        _check(
            f"recent order has >=1 line item (got {len(line_items)})",
            len(line_items) >= 1,
        )
        if line_items:
            li = line_items[0]
            _check(
                f"line_items[0].title non-empty (got {li.title!r})",
                bool(li.title),
            )
            _check(
                f"line_items[0].quantity >= 1 (got {li.quantity})",
                li.quantity >= 1,
            )
            _check(
                "line_items[0].variant_id is None (catalog sync deferred)",
                li.variant_id is None,
            )
        _check(
            f"recent order has 0 fulfillments (bulk excludes them) "
            f"(got {len(recent.fulfillments)})",
            len(recent.fulfillments) == 0,
        )
        MIN_COUNTRY_CODE_LEN = 2  # ISO 3166-1 alpha-2
        if shipping is not None:
            _check(
                f"shipping_address.country present when populated (got {shipping.country!r})",
                shipping.country is None or len(shipping.country) >= MIN_COUNTRY_CODE_LEN,
            )

    print()
    print("All bulk-sync smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
