"""TR-15 nightly reconciliation drift check.

For each store, this script:

  1. Counts webhook_events_log rows with topic LIKE 'orders/%' received in
     the last 24 hours — what the live webhook channel claims happened.
  2. Runs the bulk reconcile (`SyncService.sync_orders` with since=48h) and
     captures how many orders the bulk run upserted.
  3. Prints a side-by-side table and a one-paragraph commentary flagging
     any store where bulk found materially more orders than webhooks
     suggested — the canonical signal of dropped webhooks.

Designed to be run by Windows Task Scheduler. stdout/stderr are appended
to logs/reconcile_drift_<date>.log by the .cmd wrapper.

The script does NOT delete any rows. Bulk upserts are idempotent.

Usage (manual):
    .venv\\Scripts\\python.exe scripts\\reconcile_drift_check.py
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
from app.db.orm.store import StoreRow  # noqa: E402
from app.db.orm.webhook_event import WebhookEventRow  # noqa: E402

WINDOW_HOURS_WEBHOOK = 24
WINDOW_HOURS_BULK = 48


def _count_order_webhooks(store_id: int, since: datetime) -> int:
    factory = get_session_factory()
    with factory() as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(WebhookEventRow)
                .where(
                    WebhookEventRow.store_id == store_id,
                    WebhookEventRow.topic.like("orders/%"),
                    WebhookEventRow.received_at >= since,
                )
            )
            or 0
        )


def main() -> int:  # noqa: PLR0912, PLR0915 — linear nightly script; splitting hurts readability
    app = create_app()
    svc = app.extensions.get("sync_service")
    if svc is None:
        print("FAIL: sync_service not wired (no live store creds in .env?)")
        return 1

    configs = app.extensions["store_configs"]
    keys = sorted(configs.keys())
    if not keys:
        print("FAIL: no stores configured")
        return 1

    factory = get_session_factory()
    now = datetime.now(tz=UTC)
    webhook_since = now - timedelta(hours=WINDOW_HOURS_WEBHOOK)
    bulk_since = now - timedelta(hours=WINDOW_HOURS_BULK)

    print(f"=== TR-15 reconciliation drift check @ {now.isoformat()} ===")
    print(f"webhook window: last {WINDOW_HOURS_WEBHOOK}h  (since {webhook_since.isoformat()})")
    print(f"bulk window:    last {WINDOW_HOURS_BULK}h  (since {bulk_since.isoformat()})")
    print()

    rows: list[tuple[str, int, int, float]] = []
    for key in keys:
        with factory() as s:
            store = s.scalar(select(StoreRow).where(StoreRow.store_key == key))
        if store is None:
            print(f"  [{key}] no store row yet — skipping")
            continue

        webhook_count = _count_order_webhooks(store.id, webhook_since)
        t0 = time.monotonic()
        try:
            result = svc.sync_orders(key, since=bulk_since, max_wait_seconds=900)
            bulk_count = result.upserted
        except Exception as exc:  # noqa: BLE001
            print(f"  [{key}] FAILED: {exc!r}")
            rows.append((key, webhook_count, -1, time.monotonic() - t0))
            continue
        elapsed = time.monotonic() - t0
        rows.append((key, webhook_count, bulk_count, elapsed))

    print()
    print(f"{'store':<14} {'webhooks_24h':>14} {'bulk_upserts_48h':>18} {'elapsed_s':>10}")
    print("-" * 60)
    for key, wh, bulk, elapsed in rows:
        bulk_disp = f"{bulk}" if bulk >= 0 else "ERROR"
        print(f"{key:<14} {wh:>14} {bulk_disp:>18} {elapsed:>10.1f}")

    print()
    print("Commentary:")
    flagged: list[str] = []
    for key, wh, bulk, _ in rows:
        if bulk < 0:
            print(f"  - {key}: bulk run failed; investigate before next pass")
            flagged.append(key)
            continue
        if wh == 0 and bulk == 0:
            print(f"  - {key}: no order activity in window — quiet store or low traffic")
            continue
        # Heuristic: bulk window is 2x the webhook window, so bulk_upserts
        # ≈ 2 * webhook_count is healthy. Flag only if bulk is much higher,
        # which suggests webhooks dropped events.
        if wh > 0 and bulk > wh * 4:
            print(
                f"  - {key}: bulk found {bulk} orders but only {wh} order webhooks landed in 24h — "
                f"webhook channel may have dropped events"
            )
            flagged.append(key)
        elif wh > bulk * 2:
            print(
                f"  - {key}: webhooks claim {wh} order events but bulk only saw {bulk} — "
                f"may be churn (multiple webhooks per order); not necessarily drift"
            )
        else:
            print(f"  - {key}: counts within tolerance — webhook channel healthy")

    print()
    if flagged:
        print(f"FLAGGED stores requiring follow-up: {', '.join(flagged)}")
    else:
        print("All stores within tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
