"""Targeted backfill: re-sync order #42220 through the bulk normalizer.

End-to-end smoke test for the source_name + discountAllocations fix.
Run after applying migration 0005. Preserves the existing customer_id
linkage on the order row.
"""

from __future__ import annotations

import json
from dataclasses import replace

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.db.engine import get_engine, get_session_factory  # noqa: E402
from app.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from app.domain.models import CustomerId, StoreId  # noqa: E402
from app.repositories.products import SqlAlchemyProductRepository  # noqa: E402
from app.shopify.client import ShopifyClient  # noqa: E402
from app.shopify.config import load_store_configs  # noqa: E402
from app.shopify.normalizers.orders_bulk import normalize_order_bulk  # noqa: E402

QUERY = """
query($id: ID!) {
  order(id: $id) {
    id legacyResourceId name email phone
    processedAt createdAt updatedAt cancelledAt closedAt
    currencyCode presentmentCurrencyCode sourceName
    displayFinancialStatus displayFulfillmentStatus
    subtotalPriceSet { shopMoney { amount } presentmentMoney { amount } }
    totalPriceSet { shopMoney { amount } presentmentMoney { amount } }
    totalTaxSet { shopMoney { amount } }
    totalDiscountsSet { shopMoney { amount } }
    totalShippingPriceSet { shopMoney { amount } }
    lineItems(first: 50) {
      edges { node {
        id title sku vendor quantity
        variant { id } product { id }
        originalUnitPriceSet { shopMoney { amount } }
        totalDiscountSet { shopMoney { amount } }
        discountAllocations { allocatedAmountSet { shopMoney { amount } } }
        requiresShipping taxable
      }}
    }
  }
}
"""
GID = "gid://shopify/Order/7117040550127"


def main() -> None:
    client = ShopifyClient(load_store_configs())
    data = client.query("lubelife", QUERY, {"id": GID})
    payload = data["order"]
    payload["line_items"] = [edge["node"] for edge in payload["lineItems"]["edges"]]
    payload.pop("lineItems")

    session_factory = get_session_factory()
    eng = get_engine()

    with eng.connect() as c:
        store_id = c.execute(text("SELECT id FROM stores WHERE store_key='lubelife'")).scalar_one()
        existing_customer_id = c.execute(
            text("SELECT customer_id FROM orders WHERE gid = :g"), {"g": GID}
        ).scalar()

    with session_factory() as session:
        prods = SqlAlchemyProductRepository(session)
        variants_by_gid = prods.variant_gid_map(StoreId(store_id))
        products_by_gid = prods.product_gid_map(StoreId(store_id))

    norm = normalize_order_bulk(
        StoreId(store_id),
        payload,
        variants_by_gid=variants_by_gid,
        products_by_gid=products_by_gid,
    )

    # Preserve the existing customer_id linkage (bulk sync would otherwise
    # reset it; we don't want to lose it on a targeted re-fetch).
    order = replace(
        norm.order,
        customer_id=CustomerId(existing_customer_id) if existing_customer_id is not None else None,
    )

    print("Normalized:")
    print(f"  source_name = {order.source_name!r}")
    for li in order.line_items:
        print(f"  line {li.title!r}: qty={li.quantity}, total_discount=${li.total_discount}")
    print()

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.orders.upsert(order)
        uow.commit()

    with eng.connect() as c:
        row = c.execute(
            text(
                "SELECT id, name, source_name, total_discounts, total_price "
                "FROM orders WHERE gid = :g"
            ),
            {"g": GID},
        ).one()
        print("DB after upsert:")
        print(json.dumps(dict(row._mapping), indent=2, default=str))
        for r in c.execute(
            text(
                "SELECT title, sku, quantity, price, total_discount "
                "FROM order_line_items WHERE order_id = :oid"
            ),
            {"oid": row.id},
        ).all():
            print(f"  line: {dict(r._mapping)}")


if __name__ == "__main__":
    main()
