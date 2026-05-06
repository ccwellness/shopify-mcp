"""POST /webhooks/<store_key> — Shopify webhook receiver.

Order of operations is load-bearing (TR-3):
    1. Identify the store from the URL.
    2. Read the raw body — *before* any parse, since HMAC is over bytes.
    3. Verify the HMAC; on failure, return 401 with no DB write.
    4. Persist webhook_events_log via the ingest service (TR-14).
    5. Return 200 within Shopify's 5s budget (TR-12).
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, current_app, request

from app.services.webhook_ingest import UnknownStoreError, WebhookIngestService
from app.shopify.config import StoreConfig
from app.shopify.webhooks import is_known_topic, verify_hmac

bp = Blueprint("webhooks", __name__)


def _store_configs() -> dict[str, StoreConfig]:
    return current_app.extensions["store_configs"]  # type: ignore[no-any-return]


def _ingest_service() -> WebhookIngestService:
    return current_app.extensions["webhook_ingest"]  # type: ignore[no-any-return]


@bp.post("/webhooks/<store_key>")
def receive(store_key: str) -> Response:
    cfg = _store_configs().get(store_key)
    if cfg is None:
        abort(404, description=f"unknown store_key: {store_key!r}")

    # Raw body MUST be read before any parse. Flask caches it for later access.
    raw_body = request.get_data(cache=True, as_text=False)

    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256")
    if not verify_hmac(raw_body, cfg.webhook_secret, hmac_header):
        # Per TR-3: failure → 401, no enqueue, no DB write.
        return Response("invalid hmac", status=401, mimetype="text/plain")

    topic = request.headers.get("X-Shopify-Topic", "").strip()
    webhook_id = request.headers.get("X-Shopify-Webhook-Id")

    try:
        _ingest_service().record(
            store_key=store_key,
            topic=topic,
            shopify_webhook_id=webhook_id,
            raw_body=raw_body,
            hmac_valid=True,
        )
    except UnknownStoreError:
        # Race: store removed from config between URL match and service call.
        abort(404, description=f"unknown store_key: {store_key!r}")

    if not is_known_topic(topic):
        # Persisted for forensics, but mark visibly in the response so a
        # misconfigured registration is obvious from logs.
        return Response("topic not subscribed", status=200, mimetype="text/plain")

    # Worker dispatch (parsing + repo upserts) happens out-of-band in a
    # follow-up push. For now the row sits with processing_status='received'.
    return Response("", status=200, mimetype="text/plain")
