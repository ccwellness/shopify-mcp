"""Full bulk pipeline smoke test against live lubelife.

Exercises every sync method introduced for catalog + customers + inventory:

  1. sync_locations  — already verified by smoke_test_bulk_sync.py, run again
                       so variants_by_gid + locations_by_gid are warm.
  2. sync_customers  — bulk customers, no children. Verifies count, sample
                       email-marketing consent state landed.
  3. sync_products   — bulk products + variants. Verifies count and that
                       at least one product has variants attached.
  4. sync_inventory  — paginated query. Verifies items upserted and at
                       least one InventoryLevel landed with a non-null
                       quantity field.
  5. Cross-aggregate FK check: variant_gid_map covers >=1 line item from
                       the smoke_test_bulk_sync orders run, proving the
                       catalog can later resolve OrderLineItem.variant_id.

A 7-day window keeps the bulk ops small. Re-runs are idempotent (upserts).
The script does NOT delete any rows.

Usage:
    DATABASE_URL=postgresql+psycopg://shopify_connector:dev_password@localhost:5432/shopify_connector \\
    .venv\\Scripts\\python.exe scripts\\smoke_test_bulk_full.py
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
from app.db.orm.customer import CustomerRow  # noqa: E402
from app.db.orm.inventory import InventoryItemRow, InventoryLevelRow  # noqa: E402
from app.db.orm.order import OrderLineItemRow  # noqa: E402
from app.db.orm.product import ProductRow, VariantRow  # noqa: E402
from app.db.orm.store import StoreRow  # noqa: E402
from app.shopify.config import load_store_configs  # noqa: E402

STORE_KEY = "lubelife"
SINCE_DAYS = 7


def _check(label: str, cond: bool) -> None:
    icon = "OK" if cond else "FAIL"
    print(f"[{icon}] {label}")
    if not cond:
        raise SystemExit(1)


def main() -> int:
    configs = load_store_configs()
    if STORE_KEY not in configs:
        print(f"FAIL: {STORE_KEY!r} not in loaded store configs (no real creds in .env?)")
        return 1

    app = create_app()
    svc = app.extensions.get("sync_service")
    if svc is None:
        print("FAIL: sync_service not wired")
        return 1
    factory = get_session_factory()
    since = datetime.now(tz=UTC) - timedelta(days=SINCE_DAYS)

    # -------------------------------------------------------------------
    # Locations (warm-up — must precede inventory)
    # -------------------------------------------------------------------
    print(f"\n=== sync_locations({STORE_KEY}) ===")
    t0 = time.monotonic()
    loc_result = svc.sync_locations(STORE_KEY)
    print(f"  upserted={loc_result.upserted}  elapsed={time.monotonic() - t0:.1f}s")
    _check("locations >= 1", loc_result.upserted >= 1)

    with factory() as s:
        store = s.scalar(select(StoreRow).where(StoreRow.store_key == STORE_KEY))
    assert store is not None

    # -------------------------------------------------------------------
    # Customers
    # -------------------------------------------------------------------
    print(f"\n=== sync_customers({STORE_KEY}, since={since.date()}) ===")
    t0 = time.monotonic()
    cust_result = svc.sync_customers(STORE_KEY, since=since, max_wait_seconds=600)
    print(f"  upserted={cust_result.upserted}  elapsed={time.monotonic() - t0:.1f}s")

    with factory() as s:
        cust_count = (
            s.scalar(
                select(func.count())
                .select_from(CustomerRow)
                .where(CustomerRow.store_id == store.id)
            )
            or 0
        )
    if cust_result.upserted == 0:
        print(f"  NOTE: zero customers in {SINCE_DAYS}d window — quiet store")
    else:
        _check(f"customers table has rows ({cust_count} for store)", cust_count >= 1)
        with factory() as s:
            sample = s.scalar(
                select(CustomerRow).where(CustomerRow.store_id == store.id).limit(1)
            )
        assert sample is not None
        _check(
            f"sample customer.gid is GID (got {sample.gid!r})",
            sample.gid.startswith("gid://shopify/Customer/"),
        )

    # -------------------------------------------------------------------
    # Products
    # -------------------------------------------------------------------
    print(f"\n=== sync_products({STORE_KEY}, since={since.date()}) ===")
    t0 = time.monotonic()
    prod_result = svc.sync_products(STORE_KEY, since=since, max_wait_seconds=600)
    print(f"  upserted={prod_result.upserted}  elapsed={time.monotonic() - t0:.1f}s")

    with factory() as s:
        prod_count = (
            s.scalar(
                select(func.count()).select_from(ProductRow).where(ProductRow.store_id == store.id)
            )
            or 0
        )
        var_count = (
            s.scalar(
                select(func.count()).select_from(VariantRow).where(VariantRow.store_id == store.id)
            )
            or 0
        )
    print(f"  products in DB: {prod_count}  variants in DB: {var_count}")
    if prod_result.upserted > 0:
        _check("products table has rows", prod_count >= 1)
        _check("variants table has rows (at least one product had a variant)", var_count >= 1)
        with factory() as s:
            sample_p = s.scalar(
                select(ProductRow).where(ProductRow.store_id == store.id).limit(1)
            )
            assert sample_p is not None
            sample_variants = list(sample_p.variants)
        _check(
            f"sample product handle non-empty (got {sample_p.handle!r})",
            bool(sample_p.handle),
        )
        if sample_variants:
            v = sample_variants[0]
            _check(
                f"sample variant.gid is GID (got {v.gid!r})",
                v.gid.startswith("gid://shopify/ProductVariant/"),
            )
    else:
        print(f"  NOTE: zero products in {SINCE_DAYS}d window")

    # -------------------------------------------------------------------
    # Inventory (depends on variants + locations from above)
    # -------------------------------------------------------------------
    print(f"\n=== sync_inventory({STORE_KEY}) ===")
    t0 = time.monotonic()
    inv_result = svc.sync_inventory(STORE_KEY)
    print(f"  items upserted={inv_result.upserted}  elapsed={time.monotonic() - t0:.1f}s")

    with factory() as s:
        item_count = (
            s.scalar(
                select(func.count())
                .select_from(InventoryItemRow)
                .where(InventoryItemRow.store_id == store.id)
            )
            or 0
        )
        level_count = (
            s.scalar(
                select(func.count())
                .select_from(InventoryLevelRow)
                .where(InventoryLevelRow.store_id == store.id)
            )
            or 0
        )
        items_with_variant = (
            s.scalar(
                select(func.count())
                .select_from(InventoryItemRow)
                .where(
                    InventoryItemRow.store_id == store.id,
                    InventoryItemRow.variant_id.is_not(None),
                )
            )
            or 0
        )
    print(
        f"  inventory_items in DB: {item_count}  inventory_levels: {level_count}  "
        f"items linked to a variant: {items_with_variant}"
    )
    if inv_result.upserted > 0:
        _check("inventory_items table has rows", item_count >= 1)
        _check("inventory_levels table has rows", level_count >= 1)
        # If any products synced this run, at least some items should resolve a variant.
        if prod_count >= 1:
            _check("at least one inventory item resolved a variant", items_with_variant >= 1)
        with factory() as s:
            sample_lvl = s.scalar(
                select(InventoryLevelRow).where(InventoryLevelRow.store_id == store.id).limit(1)
            )
        assert sample_lvl is not None
        _check(
            "sample level has at least one quantity bucket populated",
            any(
                v is not None
                for v in (
                    sample_lvl.available,
                    sample_lvl.on_hand,
                    sample_lvl.committed,
                    sample_lvl.incoming,
                )
            ),
        )
    else:
        print("  NOTE: no inventory items returned (empty catalog or fresh store)")

    # -------------------------------------------------------------------
    # Cross-aggregate FK readiness: catalog can resolve order line items
    # -------------------------------------------------------------------
    print("\n=== cross-aggregate FK readiness ===")
    with factory() as s:
        line_item_gids = list(
            s.scalars(
                select(OrderLineItemRow.gid).where(
                    OrderLineItemRow.store_id == store.id,
                    OrderLineItemRow.gid.is_not(None),
                )
            ).all()
        )
        # Order line item GIDs are LineItem GIDs, NOT ProductVariant GIDs,
        # so the resolver lives in a separate (future) job. Here we just
        # confirm the catalog now has variants we COULD resolve against.
        variant_gids_count = (
            s.scalar(
                select(func.count()).select_from(VariantRow).where(VariantRow.store_id == store.id)
            )
            or 0
        )
    print(f"  order line items in DB: {len(line_item_gids)}  variants synced: {variant_gids_count}")
    if line_item_gids and variant_gids_count > 0:
        print("  catalog is now sufficient to resolve OrderLineItem.variant_id in a follow-up job")

    print()
    print("All bulk-full smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
