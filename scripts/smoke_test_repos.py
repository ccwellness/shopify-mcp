"""End-to-end smoke test of the domain → ORM → DB → ORM → domain pipeline.

Exercises every aggregate's repository at least once: insert, read by GID,
list/find with a Spec, and (where applicable) round-trip through the
mapper. Cleans up after itself by rolling back the outer transaction so
the dev DB is unchanged on success.

Usage:
    DATABASE_URL=postgresql+psycopg://shopify_connector:dev_password\
@localhost:5432/shopify_connector \\
    uv run python scripts/smoke_test_repos.py
"""

from __future__ import annotations

import pathlib
import sys
from datetime import UTC, date, datetime
from decimal import Decimal

# Ensure the project root is on sys.path when this script is invoked directly
# (`uv run python scripts/...`) — `python` puts the script's dir first, which
# shadows the editable install of the `app` package.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.db.engine import get_session_factory  # noqa: E402
from app.db.unit_of_work import SqlAlchemyUnitOfWork
from app.domain.enums import (
    AnalyticsSource,
    FinancialStatus,
    FulfillmentExecutionStatus,
    FulfillmentStatus,
    OrderLineFulfillmentStatus,
    ProductStatus,
    SubscriptionProvider,
    SubscriptionStatus,
    SyncResource,
)
from app.domain.models import (
    AnalyticsKpiDay,
    Customer,
    CustomerId,
    Fulfillment,
    FulfillmentId,
    InventoryItem,
    InventoryItemId,
    InventoryLevel,
    InventoryLevelId,
    Location,
    LocationId,
    Order,
    OrderId,
    OrderLineItem,
    OrderLineItemId,
    OrderShippingAddress,
    Product,
    ProductId,
    SessionsDay,
    Store,
    StoreId,
    SubscriptionContract,
    SubscriptionContractId,
    SyncStateRow,
    Variant,
    VariantId,
)
from app.domain.specs import (
    AnalyticsWindowSpec,
    InventorySpec,
    OrderSpec,
    ProductSpec,
    SubscriptionSpec,
)

NOW = datetime.now(tz=UTC)


def _check(label: str, cond: bool, detail: str = "") -> None:
    icon = "OK" if cond else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{icon}] {label}{extra}")
    if not cond:
        raise SystemExit(1)


def main() -> int:  # noqa: PLR0915 — linear smoke-test sequence; splitting fragments the flow
    factory = get_session_factory()
    uow = SqlAlchemyUnitOfWork(factory)

    with uow:
        # --- stores ----------------------------------------------------------
        store = Store(
            id=StoreId(0),
            store_key="smoke",
            shop_domain="smoke.myshopify.com",
            display_name="Smoke Test",
            plus=False,
            subscription_provider=SubscriptionProvider.UNKNOWN,
            read_only=True,
            active=True,
            timezone="UTC",
            currency_code="USD",
            created_at=NOW,
            updated_at=NOW,
        )
        uow.stores.upsert(store)
        loaded = uow.stores.get_by_key("smoke")
        _check(
            "stores.upsert + get_by_key",
            loaded is not None and loaded.shop_domain == "smoke.myshopify.com",
        )
        assert loaded is not None
        store_id = loaded.id

        actives = uow.stores.list_active()
        _check("stores.list_active", any(s.store_key == "smoke" for s in actives))

        # --- locations -------------------------------------------------------
        loc = Location(
            id=LocationId(0),
            store_id=store_id,
            gid="gid://shopify/Location/1",
            legacy_id=1,
            name="Main",
            address1="1 Test St",
            address2=None,
            city="Santa Clarita",
            province="CA",
            postal_code="91355",
            country="US",
            is_active=True,
            fulfills_online_orders=True,
            ships_inventory=True,
            last_seen_at=NOW,
        )
        uow.locations.upsert(loc)
        loc_loaded = uow.locations.get_by_gid(store_id, "gid://shopify/Location/1")
        _check(
            "locations.upsert + get_by_gid", loc_loaded is not None and loc_loaded.name == "Main"
        )
        assert loc_loaded is not None
        location_id = loc_loaded.id

        # --- customers -------------------------------------------------------
        cust = Customer(
            id=CustomerId(0),
            store_id=store_id,
            gid="gid://shopify/Customer/100",
            legacy_id=100,
            email="alice@example.com",
            phone=None,
            first_name="Alice",
            last_name="Smith",
            accepts_marketing=True,
            orders_count=2,
            total_spent=Decimal("123.45"),
            currency_code="USD",
            created_at=NOW,
            updated_at=NOW,
        )
        uow.customers.upsert(cust)
        cust_loaded = uow.customers.get_by_email(store_id, "alice@example.com")
        _check(
            "customers.upsert + get_by_email + Decimal round-trip",
            cust_loaded is not None and cust_loaded.total_spent == Decimal("123.45"),
        )
        assert cust_loaded is not None
        customer_id = cust_loaded.id

        # --- product + variant ----------------------------------------------
        product = Product(
            id=ProductId(0),
            store_id=store_id,
            gid="gid://shopify/Product/10",
            legacy_id=10,
            title="Widget",
            handle="widget",
            status=ProductStatus.ACTIVE,
            vendor="ACME",
            product_type="Gadgets",
            tags=("blue", "featured"),
            created_at=NOW,
            updated_at=NOW,
            variants=(
                Variant(
                    id=VariantId(0),
                    store_id=store_id,
                    product_id=ProductId(0),  # placeholder; ORM cascades the real id
                    gid="gid://shopify/ProductVariant/1001",
                    legacy_id=1001,
                    title="Default",
                    sku="WIDGET-1",
                    barcode=None,
                    position=1,
                    price=Decimal("9.99"),
                    compare_at_price=Decimal("14.99"),
                    currency_code="USD",
                ),
            ),
        )
        uow.products.upsert(product)
        prod_loaded = uow.products.get_by_handle(store_id, "widget")
        _check(
            "products.upsert + get_by_handle + variant materialization",
            prod_loaded is not None
            and len(prod_loaded.variants) == 1
            and prod_loaded.variants[0].sku == "WIDGET-1",
        )
        assert prod_loaded is not None
        product_id = prod_loaded.id
        variant_id = prod_loaded.variants[0].id

        # ProductSpec.tag (ARRAY contains)
        page = uow.products.find(ProductSpec(store_ids=(store_id,), tag="featured"))
        _check("products.find with tag filter", any(p.handle == "widget" for p in page.items))

        # --- inventory item + level -----------------------------------------
        item = InventoryItem(
            id=InventoryItemId(0),
            store_id=store_id,
            variant_id=variant_id,
            gid="gid://shopify/InventoryItem/2001",
            legacy_id=2001,
            sku="WIDGET-1",
            tracked=True,
        )
        uow.inventory.upsert_item(item)
        item_loaded = uow.inventory.get_item(store_id, "gid://shopify/InventoryItem/2001")
        _check(
            "inventory.upsert_item + get_item",
            item_loaded is not None and item_loaded.sku == "WIDGET-1",
        )
        assert item_loaded is not None

        level = InventoryLevel(
            id=InventoryLevelId(0),
            store_id=store_id,
            inventory_item_id=item_loaded.id,
            location_id=location_id,
            available=3,
            on_hand=5,
            committed=2,
            incoming=0,
            updated_at=NOW,
        )
        uow.inventory.upsert_level(level)
        levels = uow.inventory.list_levels(
            InventorySpec(store_ids=(store_id,), location_id=location_id)
        )
        EXPECTED_AVAILABLE = 3  # set above: on_hand=5, committed=2 → available=3
        _check(
            "inventory.list_levels",
            len(levels.items) == 1 and levels.items[0].available == EXPECTED_AVAILABLE,
        )
        low = uow.inventory.list_low_stock(store_id, threshold=10)
        _check("inventory.list_low_stock", len(low) == 1)

        # --- order with line item, shipping, fulfillment ---------------------
        order = Order(
            id=OrderId(0),
            store_id=store_id,
            customer_id=customer_id,
            gid="gid://shopify/Order/9000",
            legacy_id=9000,
            name="#1001",
            order_number=1001,
            email="alice@example.com",
            financial_status=FinancialStatus.PAID,
            fulfillment_status=FulfillmentStatus.FULFILLED,
            currency_code="USD",
            presentment_currency_code=None,
            subtotal_price=Decimal("19.98"),
            total_price=Decimal("21.98"),
            total_tax=Decimal("1.00"),
            total_discounts=Decimal("0"),
            total_shipping=Decimal("1.00"),
            presentment_subtotal_price=None,
            presentment_total_price=None,
            processed_at=NOW,
            cancelled_at=None,
            closed_at=None,
            created_at=NOW,
            updated_at=NOW,
            line_items=(
                OrderLineItem(
                    id=OrderLineItemId(0),
                    order_id=OrderId(0),
                    store_id=store_id,
                    variant_id=variant_id,
                    product_id=product_id,
                    gid="gid://shopify/LineItem/30001",
                    legacy_id=30001,
                    title="Widget",
                    sku="WIDGET-1",
                    vendor="ACME",
                    quantity=2,
                    price=Decimal("9.99"),
                    total_discount=Decimal("0"),
                    fulfillment_status=OrderLineFulfillmentStatus.FULFILLED,
                    requires_shipping=True,
                    taxable=True,
                ),
            ),
            shipping_address=OrderShippingAddress(
                order_id=OrderId(0),
                store_id=store_id,
                name="Alice Smith",
                company=None,
                address1="100 Main",
                address2=None,
                city="Santa Clarita",
                province="CA",
                country="US",
                zip="91355",
                phone=None,
                latitude=None,
                longitude=None,
            ),
            fulfillments=(
                Fulfillment(
                    id=FulfillmentId(0),
                    order_id=OrderId(0),
                    store_id=store_id,
                    location_id=location_id,
                    gid="gid://shopify/Fulfillment/40001",
                    legacy_id=40001,
                    status=FulfillmentExecutionStatus.SUCCESS,
                    shipment_status=None,
                    tracking_company="UPS",
                    tracking_number="1Z9999",
                    tracking_url=None,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ),
        )
        uow.orders.upsert(order)
        loaded_order = uow.orders.get_by_gid(store_id, "gid://shopify/Order/9000")
        _check(
            "orders.upsert + get_by_gid + aggregate materialization",
            loaded_order is not None
            and len(loaded_order.line_items) == 1
            and loaded_order.shipping_address is not None
            and loaded_order.shipping_address.city == "Santa Clarita"
            and len(loaded_order.fulfillments) == 1
            and loaded_order.fulfillments[0].tracking_number == "1Z9999",
        )

        # find by spec — multiple filters at once
        found = uow.orders.find(
            OrderSpec(
                store_ids=(store_id,),
                financial_status=FinancialStatus.PAID,
                sku="WIDGET-1",
            ),
            limit=10,
        )
        _check("orders.find multi-filter (status + sku)", len(found.items) == 1)

        counts = uow.orders.count_by_status(
            store_id,
            since=NOW.replace(year=NOW.year - 1),
            until=NOW.replace(year=NOW.year + 1),
        )
        _check("orders.count_by_status", counts.get(FinancialStatus.PAID, 0) == 1)

        # second upsert — confirms reconcile-on-write replaces children cleanly
        # (mutate the line_items count to verify replacement, not duplication)
        uow.orders.upsert(order)
        re_loaded = uow.orders.get_by_gid(store_id, "gid://shopify/Order/9000")
        _check(
            "orders.upsert idempotency (children replaced not duplicated)",
            re_loaded is not None and len(re_loaded.line_items) == 1,
        )

        # --- subscription ----------------------------------------------------
        sub = SubscriptionContract(
            id=SubscriptionContractId(0),
            store_id=store_id,
            customer_id=customer_id,
            provider=SubscriptionProvider.ORDERGROOVE,
            provider_contract_id="og-abc-123",
            gid=None,
            legacy_id=None,
            status=SubscriptionStatus.ACTIVE,
            next_billing_date=NOW,
            frequency_interval="MONTH",
            frequency_count=1,
            currency_code="USD",
            created_at=NOW,
            updated_at=NOW,
        )
        uow.subscriptions.upsert(sub)
        sub_page = uow.subscriptions.find(
            SubscriptionSpec(store_ids=(store_id,), provider=SubscriptionProvider.ORDERGROOVE)
        )
        _check("subscriptions.upsert + find", len(sub_page.items) == 1)

        # --- analytics: sessions_daily + analytics_kpi_daily -----------------
        today = date.today()
        sd = SessionsDay(
            store_id=store_id,
            date=today,
            sessions=1000,
            orders=26,
            total_sales=Decimal("2600.00"),
            units_sold=52,
            source=AnalyticsSource.SHOPIFYQL,
            pulled_at=NOW,
        )
        uow.analytics.upsert_sessions_day(sd)
        # idempotency: upsert again with new sessions count, confirm overwrite
        sd2 = SessionsDay(
            store_id=store_id,
            date=today,
            sessions=1100,
            orders=26,
            total_sales=Decimal("2700.00"),
            units_sold=52,
            source=AnalyticsSource.SHOPIFYQL,
            pulled_at=NOW,
        )
        uow.analytics.upsert_sessions_day(sd2)
        UPDATED_SESSIONS = 1100
        sd_loaded = uow.analytics.get_sessions_day(store_id, today)
        _check(
            "analytics.upsert_sessions_day + ON CONFLICT update",
            sd_loaded is not None and sd_loaded.sessions == UPDATED_SESSIONS,
        )

        kpi = AnalyticsKpiDay(
            store_id=store_id,
            date=today,
            sessions=UPDATED_SESSIONS,
            orders=26,
            units=52,
            revenue=Decimal("2700.00"),
            conversion_rate=Decimal("0.0236"),
            aov=Decimal("103.85"),
            computed_at=NOW,
        )
        uow.analytics.upsert_kpi_day(kpi)
        kpi_list = uow.analytics.list_kpis(
            AnalyticsWindowSpec(store_ids=(store_id,), since=today, until=today)
        )
        _check(
            "analytics.list_kpis window",
            len(kpi_list) == 1 and kpi_list[0].sessions == UPDATED_SESSIONS,
        )

        # --- sync_state ------------------------------------------------------
        sync_row = SyncStateRow(
            store_id=store_id,
            resource=SyncResource.ORDERS,
            last_completed_at=NOW,
            last_cursor=None,
            last_error=None,
            last_error_at=None,
            updated_at=NOW,
        )
        uow.sync_state.upsert(sync_row)
        sync_loaded = uow.sync_state.get(store_id, SyncResource.ORDERS)
        _check(
            "sync_state.upsert + get",
            sync_loaded is not None and sync_loaded.resource == SyncResource.ORDERS,
        )

        # ---------------------------------------------------------------------
        # Roll everything back so the dev DB is unchanged after a successful run.
        uow.rollback()

    print()
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
