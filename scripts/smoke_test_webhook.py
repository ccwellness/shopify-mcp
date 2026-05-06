"""Webhook receiver smoke test (uses Flask's test_client — no live server).

Covers:
  - valid HMAC + known topic        -> 200, row landed in webhook_events_log
  - valid HMAC + unknown topic      -> 200 ("topic not subscribed"), still persisted
  - invalid HMAC                    -> 401, NO row written (TR-3)
  - missing HMAC header             -> 401
  - unknown store_key               -> 404
  - first webhook auto-creates the StoreRow from .env config
  - gzip round-trip: stored payload decompresses to the original raw body
  - second valid webhook for same store reuses the existing StoreRow

All test rows are committed inside the service's UoW; this script then
opens its own UoW to delete them, so the dev DB is clean on success.

Usage:
    DATABASE_URL=postgresql+psycopg://shopify_connector:dev_password@localhost:5432/shopify_connector \\
    uv run python scripts/smoke_test_webhook.py
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import delete, select  # noqa: E402

from app import create_app  # noqa: E402
from app.db.engine import get_session_factory  # noqa: E402
from app.db.orm.store import StoreRow  # noqa: E402
from app.db.orm.webhook_event import WebhookEventRow  # noqa: E402
from app.shopify.config import load_store_configs  # noqa: E402
from app.shopify.webhooks import compute_hmac  # noqa: E402

STORE_KEY = "lubelife"


def _check(label: str, cond: bool, detail: str = "") -> None:
    icon = "OK" if cond else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{icon}] {label}{extra}")
    if not cond:
        raise SystemExit(1)


def _post(client, store_key: str, body: bytes, headers: dict[str, str]):
    return client.post(
        f"/webhooks/{store_key}",
        data=body,
        headers=headers,
        content_type="application/json",
    )


def main() -> int:
    configs = load_store_configs()
    if STORE_KEY not in configs:
        print(f"FAIL: {STORE_KEY!r} not in loaded store configs; check .env")
        return 1
    cfg = configs[STORE_KEY]

    app = create_app()
    client = app.test_client()
    session_factory = get_session_factory()

    # Sample payloads — minimal but realistic.
    order_payload = json.dumps(
        {"id": 9_000_000_001, "name": "#TEST-1001", "test": True}
    ).encode("utf-8")
    unknown_topic_payload = b'{"hello": "world"}'

    valid_signature = compute_hmac(order_payload, cfg.webhook_secret)
    invalid_signature = "Y" * 44  # garbage of the right length

    try:
        # 1) Unknown store -> 404
        resp = _post(
            client,
            "nonexistent",
            order_payload,
            {
                "X-Shopify-Topic": "orders/create",
                "X-Shopify-Hmac-Sha256": valid_signature,
                "X-Shopify-Webhook-Id": "wh-404-1",
            },
        )
        _check("unknown store_key returns 404", resp.status_code == 404)

        # 2) Missing HMAC -> 401
        resp = _post(
            client,
            STORE_KEY,
            order_payload,
            {"X-Shopify-Topic": "orders/create", "X-Shopify-Webhook-Id": "wh-noauth-1"},
        )
        _check("missing HMAC header -> 401", resp.status_code == 401)

        # 3) Invalid HMAC -> 401, no row written (TR-3)
        resp = _post(
            client,
            STORE_KEY,
            order_payload,
            {
                "X-Shopify-Topic": "orders/create",
                "X-Shopify-Hmac-Sha256": invalid_signature,
                "X-Shopify-Webhook-Id": "wh-bad-1",
            },
        )
        _check("invalid HMAC -> 401", resp.status_code == 401)
        with session_factory() as s:
            row_count = s.scalar(
                select(WebhookEventRow).where(WebhookEventRow.shopify_webhook_id == "wh-bad-1")
            )
        _check("invalid HMAC writes NO row (TR-3)", row_count is None)

        # 4) Valid HMAC + known topic -> 200, row written, gzip round-trips
        resp = _post(
            client,
            STORE_KEY,
            order_payload,
            {
                "X-Shopify-Topic": "orders/create",
                "X-Shopify-Hmac-Sha256": valid_signature,
                "X-Shopify-Webhook-Id": "wh-good-1",
            },
        )
        _check(
            f"valid HMAC + orders/create -> 200 (got {resp.status_code})",
            resp.status_code == 200,
        )
        with session_factory() as s:
            row = s.scalar(
                select(WebhookEventRow).where(WebhookEventRow.shopify_webhook_id == "wh-good-1")
            )
        _check("row landed in webhook_events_log", row is not None)
        assert row is not None
        _check("row.topic = orders/create", row.topic == "orders/create")
        _check("row.hmac_valid = True", row.hmac_valid is True)
        _check("row.processing_status = 'received'", row.processing_status == "received")
        decompressed = gzip.decompress(row.payload_compressed)
        _check("payload gzip round-trip matches original", decompressed == order_payload)
        _check(
            "row.payload_size = raw body length",
            row.payload_size == len(order_payload),
            f"{row.payload_size} == {len(order_payload)}",
        )

        # 5) StoreRow auto-created from config on first webhook
        with session_factory() as s:
            store_row = s.scalar(select(StoreRow).where(StoreRow.store_key == STORE_KEY))
        _check("StoreRow auto-created from .env config", store_row is not None)
        assert store_row is not None
        _check(
            "auto-created StoreRow has shop_domain from config",
            store_row.shop_domain == cfg.shop_domain,
        )

        # 6) Unknown topic -> 200 but body says "topic not subscribed", still persisted
        unknown_sig = compute_hmac(unknown_topic_payload, cfg.webhook_secret)
        resp = _post(
            client,
            STORE_KEY,
            unknown_topic_payload,
            {
                "X-Shopify-Topic": "themes/publish",
                "X-Shopify-Hmac-Sha256": unknown_sig,
                "X-Shopify-Webhook-Id": "wh-unknown-1",
            },
        )
        _check("unknown topic with valid HMAC -> 200", resp.status_code == 200)
        _check(
            "body indicates topic not subscribed",
            b"topic not subscribed" in resp.data,
            f"got {resp.data!r}",
        )
        with session_factory() as s:
            row2 = s.scalar(
                select(WebhookEventRow).where(WebhookEventRow.shopify_webhook_id == "wh-unknown-1")
            )
        _check("unknown-topic delivery still persisted (forensics)", row2 is not None)

        # 7) Second valid webhook reuses existing StoreRow (no duplicate)
        resp = _post(
            client,
            STORE_KEY,
            order_payload,
            {
                "X-Shopify-Topic": "orders/updated",
                "X-Shopify-Hmac-Sha256": valid_signature,
                "X-Shopify-Webhook-Id": "wh-good-2",
            },
        )
        _check("second webhook -> 200", resp.status_code == 200)
        with session_factory() as s:
            store_count = len(
                s.scalars(select(StoreRow).where(StoreRow.store_key == STORE_KEY)).all()
            )
        _check("StoreRow not duplicated on second webhook", store_count == 1)

    finally:
        # Clean up — delete the rows this test created, then the auto-created
        # StoreRow if it has no other references. webhook_events_log first
        # because of the FK.
        with session_factory() as s:
            s.execute(
                delete(WebhookEventRow).where(
                    WebhookEventRow.shopify_webhook_id.in_(
                        ["wh-good-1", "wh-good-2", "wh-unknown-1"]
                    )
                )
            )
            # Only delete the StoreRow if no other tables reference it.
            s.execute(delete(StoreRow).where(StoreRow.store_key == STORE_KEY))
            s.commit()

    print()
    print("All webhook smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
