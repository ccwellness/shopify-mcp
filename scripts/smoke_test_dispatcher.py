"""End-to-end dispatcher smoke test.

Synthesizes a Shopify orders/create webhook with a realistic nested
payload (customer + line items + shipping address + fulfillments),
POSTs it through the receiver, and verifies that the dispatcher:

  1. Marks the webhook_events_log row processed=True.
  2. Upserts the customer.
  3. Upserts the order with all children materialized.
  4. Links order.customer_id to the customer's DB id.

Also covers:
  - orders/updated re-runs on the same payload (idempotent — line items
    don't duplicate, customer doesn't get a second row).
  - An unimplemented topic (products/create) is recorded then marked
    'failed' with the "not yet implemented" message.

Cleans up after itself by deleting all rows it created so the dev DB is
unchanged on success.

Usage:
    DATABASE_URL=postgresql+psycopg://shopify_connector:dev_password\
@localhost:5432/shopify_connector \\
    uv run python scripts/smoke_test_dispatcher.py
"""

from __future__ import annotations

import json
import pathlib
import sys
from http import HTTPStatus

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import delete, select  # noqa: E402

from app import create_app  # noqa: E402
from app.db.engine import get_session_factory  # noqa: E402
from app.db.orm.customer import CustomerRow  # noqa: E402
from app.db.orm.order import (  # noqa: E402
    OrderRow,
)
from app.db.orm.store import StoreRow  # noqa: E402
from app.db.orm.webhook_event import WebhookEventRow  # noqa: E402
from app.shopify.config import load_store_configs  # noqa: E402
from app.shopify.webhooks import compute_hmac  # noqa: E402

STORE_KEY = "lubelife"
ORDER_GID = "gid://shopify/Order/9999999001"
CUSTOMER_GID = "gid://shopify/Customer/9999999100"

# Test-data invariants — referenced from multiple assertions below.
INITIAL_LINE_QTY = 2
UPDATED_LINE_QTY = 5
EXPECTED_LINE_ITEMS = 1
EXPECTED_FULFILLMENTS = 1
EXPECTED_CUSTOMER_ROWS = 1


def _check(label: str, cond: bool, detail: str = "") -> None:
    icon = "OK" if cond else "FAIL"
    extra = f" -- {detail}" if detail else ""
    print(f"[{icon}] {label}{extra}")
    if not cond:
        raise SystemExit(1)


def _post(client, store_key, body, headers):
    return client.post(
        f"/webhooks/{store_key}",
        data=body,
        headers=headers,
        content_type="application/json",
    )


def _order_payload(line_qty: int = INITIAL_LINE_QTY) -> bytes:
    return json.dumps(
        {
            "id": 9_999_999_001,
            "admin_graphql_api_id": ORDER_GID,
            "name": "#TEST-9001",
            "order_number": 9001,
            "email": "smoke@example.com",
            "phone": None,
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
            "currency": "USD",
            "presentment_currency": "USD",
            "subtotal_price": "19.98",
            "total_price": "21.98",
            "total_tax": "1.00",
            "total_discounts": "0.00",
            "total_shipping_price_set": {
                "shop_money": {"amount": "1.00", "currency_code": "USD"},
                "presentment_money": {"amount": "1.00", "currency_code": "USD"},
            },
            "subtotal_price_set": {
                "shop_money": {"amount": "19.98", "currency_code": "USD"},
                "presentment_money": {"amount": "19.98", "currency_code": "USD"},
            },
            "total_price_set": {
                "shop_money": {"amount": "21.98", "currency_code": "USD"},
                "presentment_money": {"amount": "21.98", "currency_code": "USD"},
            },
            "processed_at": "2026-04-29T15:00:00-04:00",
            "created_at": "2026-04-29T15:00:00-04:00",
            "updated_at": "2026-04-29T15:00:00-04:00",
            "cancelled_at": None,
            "closed_at": None,
            "customer": {
                "id": 9_999_999_100,
                "admin_graphql_api_id": CUSTOMER_GID,
                "email": "smoke@example.com",
                "phone": None,
                "first_name": "Smoke",
                "last_name": "Test",
                "accepts_marketing": True,
                "orders_count": 1,
                "total_spent": "21.98",
                "currency": "USD",
                "created_at": "2026-04-29T14:00:00-04:00",
                "updated_at": "2026-04-29T15:00:00-04:00",
            },
            "shipping_address": {
                "first_name": "Smoke",
                "last_name": "Test",
                "company": None,
                "address1": "100 Test St",
                "address2": None,
                "city": "Santa Clarita",
                "province": "CA",
                "country": "US",
                "zip": "91355",
                "phone": None,
                "latitude": "34.3917",
                "longitude": "-118.5426",
            },
            "line_items": [
                {
                    "id": 9_999_999_201,
                    "admin_graphql_api_id": "gid://shopify/LineItem/9999999201",
                    "variant_id": 8_888_888_001,
                    "product_id": 7_777_777_001,
                    "title": "Smoke Widget",
                    "sku": "SMOKE-1",
                    "vendor": "ACME",
                    "quantity": line_qty,
                    "price": "9.99",
                    "total_discount": "0.00",
                    "fulfillment_status": "fulfilled",
                    "requires_shipping": True,
                    "taxable": True,
                }
            ],
            "fulfillments": [
                {
                    "id": 9_999_999_301,
                    "admin_graphql_api_id": "gid://shopify/Fulfillment/9999999301",
                    "status": "success",
                    "shipment_status": None,
                    "tracking_company": "UPS",
                    "tracking_number": "1Z9SMOKE",
                    "tracking_url": None,
                    "created_at": "2026-04-29T16:00:00-04:00",
                    "updated_at": "2026-04-29T16:00:00-04:00",
                }
            ],
        }
    ).encode("utf-8")


def _cleanup() -> None:
    factory = get_session_factory()
    with factory() as s:
        s.execute(delete(WebhookEventRow).where(WebhookEventRow.shopify_webhook_id.like("smoke-%")))
        s.execute(delete(OrderRow).where(OrderRow.gid == ORDER_GID))
        s.execute(delete(CustomerRow).where(CustomerRow.gid == CUSTOMER_GID))
        s.execute(delete(StoreRow).where(StoreRow.store_key == STORE_KEY))
        s.commit()


def main() -> int:  # noqa: PLR0915 — linear smoke-test sequence; splitting fragments the flow
    configs = load_store_configs()
    if STORE_KEY not in configs:
        print(f"FAIL: {STORE_KEY!r} not in loaded store configs")
        return 1
    cfg = configs[STORE_KEY]

    app = create_app()
    client = app.test_client()
    factory = get_session_factory()

    body1 = _order_payload(line_qty=INITIAL_LINE_QTY)
    sig1 = compute_hmac(body1, cfg.webhook_secret)

    try:
        # 1) orders/create — full nested payload
        resp = _post(
            client,
            STORE_KEY,
            body1,
            {
                "X-Shopify-Topic": "orders/create",
                "X-Shopify-Hmac-Sha256": sig1,
                "X-Shopify-Webhook-Id": "smoke-create-1",
            },
        )
        _check(f"orders/create -> 200 (got {resp.status_code})", resp.status_code == HTTPStatus.OK)

        with factory() as s:
            event = s.scalar(
                select(WebhookEventRow).where(
                    WebhookEventRow.shopify_webhook_id == "smoke-create-1"
                )
            )
        _check("event row exists", event is not None)
        assert event is not None
        _check(
            f"event.processing_status='processed' (got {event.processing_status})",
            event.processing_status == "processed",
        )
        _check(f"event.error is None (got {event.error!r})", event.error is None)

        with factory() as s:
            order = s.scalar(select(OrderRow).where(OrderRow.gid == ORDER_GID))
            customer = s.scalar(select(CustomerRow).where(CustomerRow.gid == CUSTOMER_GID))
        _check("order row landed", order is not None)
        _check("customer row landed", customer is not None)
        assert order is not None
        assert customer is not None
        _check(
            f"order.customer_id == customer.id ({order.customer_id} == {customer.id})",
            order.customer_id == customer.id,
        )
        _check(
            f"order.financial_status='paid' (got {order.financial_status!r})",
            order.financial_status == "paid",
        )
        _check(
            f"order.total_price == 21.98 (got {order.total_price})",
            str(order.total_price) == "21.9800",
        )
        _check(
            f"order.total_shipping == 1.00 (got {order.total_shipping})",
            str(order.total_shipping) == "1.0000",
        )

        with factory() as s:
            order_full = s.scalar(select(OrderRow).where(OrderRow.gid == ORDER_GID))
            assert order_full is not None
            line_items = order_full.line_items
            shipping = order_full.shipping_address
            fulfillments = order_full.fulfillments
        _check(
            f"order has 1 line item (got {len(line_items)})",
            len(line_items) == EXPECTED_LINE_ITEMS,
        )
        _check(
            f"line_items[0].sku='SMOKE-1' (got {line_items[0].sku!r})",
            line_items[0].sku == "SMOKE-1",
        )
        _check(
            f"line_items[0].quantity={INITIAL_LINE_QTY} (got {line_items[0].quantity})",
            line_items[0].quantity == INITIAL_LINE_QTY,
        )
        _check(
            "line_items[0].variant_id=None (catalog sync deferred)",
            line_items[0].variant_id is None,
        )
        _check("shipping_address present", shipping is not None)
        assert shipping is not None
        _check(
            f"shipping.city='Santa Clarita' (got {shipping.city!r})",
            shipping.city == "Santa Clarita",
        )
        _check(
            f"order has 1 fulfillment (got {len(fulfillments)})",
            len(fulfillments) == EXPECTED_FULFILLMENTS,
        )
        _check(
            f"fulfillment.tracking_number='1Z9SMOKE' (got {fulfillments[0].tracking_number!r})",
            fulfillments[0].tracking_number == "1Z9SMOKE",
        )

        # 2) orders/updated re-runs on same payload — idempotency
        body2 = _order_payload(line_qty=UPDATED_LINE_QTY)  # mutate quantity to verify update
        sig2 = compute_hmac(body2, cfg.webhook_secret)
        resp = _post(
            client,
            STORE_KEY,
            body2,
            {
                "X-Shopify-Topic": "orders/updated",
                "X-Shopify-Hmac-Sha256": sig2,
                "X-Shopify-Webhook-Id": "smoke-update-1",
            },
        )
        _check(f"orders/updated -> 200 (got {resp.status_code})", resp.status_code == HTTPStatus.OK)
        with factory() as s:
            order2 = s.scalar(select(OrderRow).where(OrderRow.gid == ORDER_GID))
            assert order2 is not None
            line_items2 = order2.line_items
        _check(
            f"still 1 line item after update (got {len(line_items2)})",
            len(line_items2) == EXPECTED_LINE_ITEMS,
        )
        _check(
            f"quantity updated to {UPDATED_LINE_QTY} (got {line_items2[0].quantity})",
            line_items2[0].quantity == UPDATED_LINE_QTY,
        )
        with factory() as s:
            cust_count = len(
                s.scalars(select(CustomerRow).where(CustomerRow.gid == CUSTOMER_GID)).all()
            )
        _check(
            f"customer not duplicated on update (got {cust_count} rows)",
            cust_count == EXPECTED_CUSTOMER_ROWS,
        )

        # 3) Unimplemented topic — products/create — recorded, marked failed
        prod_body = b'{"id": 1, "title": "Whatever"}'
        prod_sig = compute_hmac(prod_body, cfg.webhook_secret)
        resp = _post(
            client,
            STORE_KEY,
            prod_body,
            {
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Hmac-Sha256": prod_sig,
                "X-Shopify-Webhook-Id": "smoke-prod-1",
            },
        )
        _check(
            f"products/create -> 200 (got {resp.status_code})", resp.status_code == HTTPStatus.OK
        )
        with factory() as s:
            prod_event = s.scalar(
                select(WebhookEventRow).where(WebhookEventRow.shopify_webhook_id == "smoke-prod-1")
            )
        _check("products/create event row exists", prod_event is not None)
        assert prod_event is not None
        _check(
            f"products/create marked failed (got {prod_event.processing_status!r})",
            prod_event.processing_status == "failed",
        )
        _check(
            f"failure error mentions 'not yet implemented' (got {prod_event.error!r})",
            prod_event.error is not None and "not yet implemented" in prod_event.error,
        )

    finally:
        _cleanup()

    print()
    print("All dispatcher smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
