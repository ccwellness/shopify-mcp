"""Dry-run for OrderRepository.aggregate_in_window.

Prints per-store rollups for every store with a row in the local DB so
the operator can sanity-check the new method's output before more
service / route code is wired on top.

Usage (from project root, with .env loaded):
    .venv\\Scripts\\python.exe scripts\\aggregate_orders_dryrun.py
    .venv\\Scripts\\python.exe scripts\\aggregate_orders_dryrun.py --days 30
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

from app.db.engine import get_session_factory  # noqa: E402
from app.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from app.domain.models import StoreId  # noqa: E402


def _print_aggregate(store_key: str, agg: object) -> None:
    # Avoid importing OrderAggregate just for typing — we know its shape.
    print(f"  store_id:      {agg.store_id}")  # type: ignore[attr-defined]
    print(f"  count:         {agg.count}")  # type: ignore[attr-defined]
    print(f"  revenue (paid): {agg.revenue} {agg.currency_code or '?'}")  # type: ignore[attr-defined]
    print(f"  units (paid):  {agg.units}")  # type: ignore[attr-defined]
    print("  status_counts:")
    for status, n in sorted(agg.status_counts.items(), key=lambda kv: kv[0].value):  # type: ignore[attr-defined]
        print(f"    {status.value:<22} {n}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    until = datetime.now(tz=UTC)
    since = until - timedelta(days=args.days)

    print(f"=== OrderRepository.aggregate_in_window dry run (last {args.days}d) ===")
    print(f"window: {since.isoformat()}  ->  {until.isoformat()}")
    print()

    factory = get_session_factory()
    uow = SqlAlchemyUnitOfWork(factory)
    with uow as u:
        stores = u.stores.list_active()

    if not stores:
        print("No active stores in DB. Run `uv run flask sync init --store <key>` first.")
        return 0

    for store in sorted(stores, key=lambda s: s.store_key):
        print(f"[{store.store_key}]")
        with uow as u:
            agg = u.orders.aggregate_in_window(StoreId(int(store.id)), since, until)
        _print_aggregate(store.store_key, agg)
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
