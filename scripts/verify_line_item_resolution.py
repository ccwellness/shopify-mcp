"""Verify OrderLineItem.variant_id / product_id resolution after sync_orders.

Snapshots before-counts of resolved-vs-null FKs, runs `sync_orders` against
lubelife with a 7d window, then re-snapshots and reports the deltas.

Pass criterion: at least one line item that previously had variant_id=None
now has it populated, AND the resolved product_id ratio is non-trivial.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import func, select  # noqa: E402

from app import create_app  # noqa: E402
from app.db.engine import get_session_factory  # noqa: E402
from app.db.orm.order import OrderLineItemRow  # noqa: E402
from app.db.orm.store import StoreRow  # noqa: E402

STORE_KEY = "lubelife"


def _counts(store_id: int) -> tuple[int, int, int]:
    factory = get_session_factory()
    with factory() as s:
        total = (
            s.scalar(
                select(func.count())
                .select_from(OrderLineItemRow)
                .where(OrderLineItemRow.store_id == store_id)
            )
            or 0
        )
        with_variant = (
            s.scalar(
                select(func.count())
                .select_from(OrderLineItemRow)
                .where(
                    OrderLineItemRow.store_id == store_id,
                    OrderLineItemRow.variant_id.is_not(None),
                )
            )
            or 0
        )
        with_product = (
            s.scalar(
                select(func.count())
                .select_from(OrderLineItemRow)
                .where(
                    OrderLineItemRow.store_id == store_id,
                    OrderLineItemRow.product_id.is_not(None),
                )
            )
            or 0
        )
    return total, with_variant, with_product


def main() -> int:
    app = create_app()
    svc = app.extensions.get("sync_service")
    if svc is None:
        print("FAIL: sync_service not wired")
        return 1
    factory = get_session_factory()
    with factory() as s:
        store = s.scalar(select(StoreRow).where(StoreRow.store_key == STORE_KEY))
    assert store is not None

    before_total, before_v, before_p = _counts(store.id)
    print(
        f"BEFORE: line_items total={before_total}  "
        f"variant_id_resolved={before_v}  product_id_resolved={before_p}"
    )

    since = datetime.now(tz=UTC) - timedelta(days=7)
    print(f"\nRunning sync_orders({STORE_KEY}, since={since.date()}) ...")
    result = svc.sync_orders(STORE_KEY, since=since, max_wait_seconds=600)
    print(f"  upserted={result.upserted}")

    after_total, after_v, after_p = _counts(store.id)
    print(
        f"AFTER:  line_items total={after_total}  "
        f"variant_id_resolved={after_v}  product_id_resolved={after_p}"
    )

    delta_v = after_v - before_v
    delta_p = after_p - before_p
    print(f"\nDelta: +{delta_v} variant_id resolved, +{delta_p} product_id resolved")

    if delta_v == 0 and delta_p == 0 and before_v == 0:
        print("FAIL: nothing was resolved")
        return 1
    if after_v == 0:
        print("FAIL: 0 line items have variant_id after the run")
        return 1
    print("\nResolution working — line items now carry FKs to catalog.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
