"""Initial multi-store schema.

Covers every table in TR-17 with TR-18 compound indexes, TR-19 numeric(19,4)
money columns (plus separate presentment-currency columns where they may
differ on orders), and TR-20 GID + parsed-numeric pairs on every
Shopify-sourced row.

Revision ID: 0001
Revises:
Create Date: 2026-05-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MONEY = sa.Numeric(19, 4)
TS = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")


def upgrade() -> None:
    # ---- stores ------------------------------------------------------------
    op.create_table(
        "stores",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_key", sa.Text, nullable=False),
        sa.Column("shop_domain", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("plus", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "subscription_provider", sa.Text, nullable=False, server_default=sa.text("'unknown'")
        ),
        sa.Column("read_only", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("timezone", sa.Text, nullable=True),
        sa.Column("currency_code", sa.Text, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("store_key", name="uq_stores_store_key"),
        sa.UniqueConstraint("shop_domain", name="uq_stores_shop_domain"),
    )

    # ---- locations ---------------------------------------------------------
    op.create_table(
        "locations",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("gid", sa.Text, nullable=False),
        sa.Column("legacy_id", sa.BigInteger, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("address1", sa.Text, nullable=True),
        sa.Column("address2", sa.Text, nullable=True),
        sa.Column("city", sa.Text, nullable=True),
        sa.Column("province", sa.Text, nullable=True),
        sa.Column("postal_code", sa.Text, nullable=True),
        sa.Column("country", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("fulfills_online_orders", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ships_inventory", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("store_id", "gid", name="uq_locations_store_gid"),
    )
    op.create_index("ix_locations_store_legacy_id", "locations", ["store_id", "legacy_id"])

    # ---- customers ---------------------------------------------------------
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("gid", sa.Text, nullable=False),
        sa.Column("legacy_id", sa.BigInteger, nullable=False),
        sa.Column("email", sa.Text, nullable=True),
        sa.Column("phone", sa.Text, nullable=True),
        sa.Column("first_name", sa.Text, nullable=True),
        sa.Column("last_name", sa.Text, nullable=True),
        sa.Column("accepts_marketing", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("orders_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_spent", MONEY, nullable=False, server_default="0"),
        sa.Column("currency_code", sa.Text, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("store_id", "gid", name="uq_customers_store_gid"),
    )
    op.create_index("ix_customers_store_email", "customers", ["store_id", "email"])

    # ---- products ----------------------------------------------------------
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("gid", sa.Text, nullable=False),
        sa.Column("legacy_id", sa.BigInteger, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("handle", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'active'")),
        sa.Column("vendor", sa.Text, nullable=True),
        sa.Column("product_type", sa.Text, nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("store_id", "gid", name="uq_products_store_gid"),
    )
    op.create_index("ix_products_store_handle", "products", ["store_id", "handle"])

    # ---- variants ----------------------------------------------------------
    op.create_table(
        "variants",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "product_id",
            sa.BigInteger,
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gid", sa.Text, nullable=False),
        sa.Column("legacy_id", sa.BigInteger, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("sku", sa.Text, nullable=True),
        sa.Column("barcode", sa.Text, nullable=True),
        sa.Column("position", sa.Integer, nullable=True),
        sa.Column("price", MONEY, nullable=False, server_default="0"),
        sa.Column("compare_at_price", MONEY, nullable=True),
        sa.Column("currency_code", sa.Text, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("store_id", "gid", name="uq_variants_store_gid"),
    )
    # TR-18: (store_id, sku)
    op.create_index("ix_variants_store_sku", "variants", ["store_id", "sku"])

    # ---- inventory_items ---------------------------------------------------
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "variant_id",
            sa.BigInteger,
            sa.ForeignKey("variants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("gid", sa.Text, nullable=False),
        sa.Column("legacy_id", sa.BigInteger, nullable=False),
        sa.Column("sku", sa.Text, nullable=True),
        sa.Column("tracked", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("store_id", "gid", name="uq_inventory_items_store_gid"),
    )
    op.create_index("ix_inventory_items_store_sku", "inventory_items", ["store_id", "sku"])

    # ---- inventory_levels --------------------------------------------------
    op.create_table(
        "inventory_levels",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "inventory_item_id",
            sa.BigInteger,
            sa.ForeignKey("inventory_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "location_id",
            sa.BigInteger,
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("available", sa.Integer, nullable=True),
        sa.Column("on_hand", sa.Integer, nullable=True),
        sa.Column("committed", sa.Integer, nullable=True),
        sa.Column("incoming", sa.Integer, nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "store_id",
            "inventory_item_id",
            "location_id",
            name="uq_inventory_levels_store_item_location",
        ),
    )
    # TR-18: (store_id, location_id)
    op.create_index(
        "ix_inventory_levels_store_location", "inventory_levels", ["store_id", "location_id"]
    )

    # ---- orders ------------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "customer_id",
            sa.BigInteger,
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("gid", sa.Text, nullable=False),
        sa.Column("legacy_id", sa.BigInteger, nullable=False),
        sa.Column("name", sa.Text, nullable=False),  # human-readable, e.g. "#1001"
        sa.Column("order_number", sa.Integer, nullable=True),
        sa.Column("email", sa.Text, nullable=True),
        sa.Column("financial_status", sa.Text, nullable=True),
        sa.Column("fulfillment_status", sa.Text, nullable=True),
        sa.Column("currency_code", sa.Text, nullable=False),
        sa.Column("presentment_currency_code", sa.Text, nullable=True),
        sa.Column("subtotal_price", MONEY, nullable=False, server_default="0"),
        sa.Column("total_price", MONEY, nullable=False, server_default="0"),
        sa.Column("total_tax", MONEY, nullable=False, server_default="0"),
        sa.Column("total_discounts", MONEY, nullable=False, server_default="0"),
        sa.Column("total_shipping", MONEY, nullable=False, server_default="0"),
        # Per TR-19, separate columns where presentment currency differs.
        sa.Column("presentment_subtotal_price", MONEY, nullable=True),
        sa.Column("presentment_total_price", MONEY, nullable=True),
        sa.Column("processed_at", TS, nullable=False),
        sa.Column("cancelled_at", TS, nullable=True),
        sa.Column("closed_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("store_id", "gid", name="uq_orders_store_gid"),
    )
    # TR-18: (store_id, processed_at) — primary listing index.
    op.create_index("ix_orders_store_processed_at", "orders", ["store_id", "processed_at"])
    op.create_index("ix_orders_store_order_number", "orders", ["store_id", "order_number"])
    op.create_index("ix_orders_store_email", "orders", ["store_id", "email"])
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])

    # ---- order_line_items --------------------------------------------------
    op.create_table(
        "order_line_items",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "order_id",
            sa.BigInteger,
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "variant_id",
            sa.BigInteger,
            sa.ForeignKey("variants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "product_id",
            sa.BigInteger,
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("gid", sa.Text, nullable=True),
        sa.Column("legacy_id", sa.BigInteger, nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("sku", sa.Text, nullable=True),
        sa.Column("vendor", sa.Text, nullable=True),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("price", MONEY, nullable=False, server_default="0"),
        sa.Column("total_discount", MONEY, nullable=False, server_default="0"),
        sa.Column("fulfillment_status", sa.Text, nullable=True),
        sa.Column("requires_shipping", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("taxable", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_order_line_items_order_id", "order_line_items", ["order_id"])
    # TR-18: (store_id, sku) on order lines too — most cross-store SKU reports hit this table.
    op.create_index("ix_order_line_items_store_sku", "order_line_items", ["store_id", "sku"])

    # ---- order_shipping_addresses ------------------------------------------
    op.create_table(
        "order_shipping_addresses",
        sa.Column(
            "order_id",
            sa.BigInteger,
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("company", sa.Text, nullable=True),
        sa.Column("address1", sa.Text, nullable=True),
        sa.Column("address2", sa.Text, nullable=True),
        sa.Column("city", sa.Text, nullable=True),
        sa.Column("province", sa.Text, nullable=True),
        sa.Column("country", sa.Text, nullable=True),
        sa.Column("zip", sa.Text, nullable=True),
        sa.Column("phone", sa.Text, nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
    )

    # ---- fulfillments ------------------------------------------------------
    op.create_table(
        "fulfillments",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "order_id",
            sa.BigInteger,
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "location_id",
            sa.BigInteger,
            sa.ForeignKey("locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("gid", sa.Text, nullable=False),
        sa.Column("legacy_id", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("shipment_status", sa.Text, nullable=True),
        sa.Column("tracking_company", sa.Text, nullable=True),
        sa.Column("tracking_number", sa.Text, nullable=True),
        sa.Column("tracking_url", sa.Text, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("store_id", "gid", name="uq_fulfillments_store_gid"),
    )
    op.create_index("ix_fulfillments_order_id", "fulfillments", ["order_id"])
    op.create_index("ix_fulfillments_store_created_at", "fulfillments", ["store_id", "created_at"])
    op.create_index("ix_fulfillments_store_location", "fulfillments", ["store_id", "location_id"])

    # ---- subscription_contracts --------------------------------------------
    op.create_table(
        "subscription_contracts",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "customer_id",
            sa.BigInteger,
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.Text, nullable=False),  # "native" | "ordergroove" | ...
        sa.Column("provider_contract_id", sa.Text, nullable=False),
        sa.Column("gid", sa.Text, nullable=True),  # only set when provider="native"
        sa.Column("legacy_id", sa.BigInteger, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("next_billing_date", TS, nullable=True),
        sa.Column("frequency_interval", sa.Text, nullable=True),
        sa.Column("frequency_count", sa.Integer, nullable=True),
        sa.Column("currency_code", sa.Text, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "store_id",
            "provider",
            "provider_contract_id",
            name="uq_subscription_contracts_provider_id",
        ),
    )
    op.create_index(
        "ix_subscription_contracts_store_status", "subscription_contracts", ["store_id", "status"]
    )

    # ---- sessions_daily ----------------------------------------------------
    op.create_table(
        "sessions_daily",
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), primary_key=True),
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("sessions", sa.Integer, nullable=True),
        sa.Column("orders", sa.Integer, nullable=True),
        sa.Column("total_sales", MONEY, nullable=True),
        sa.Column("units_sold", sa.Integer, nullable=True),
        sa.Column(
            "source", sa.Text, nullable=False, server_default=sa.text("'shopifyql'")
        ),  # "shopifyql" | "ga4"
        sa.Column("pulled_at", TS, nullable=False, server_default=NOW),
    )

    # ---- analytics_kpi_daily -----------------------------------------------
    op.create_table(
        "analytics_kpi_daily",
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), primary_key=True),
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("sessions", sa.Integer, nullable=True),
        sa.Column("orders", sa.Integer, nullable=True),
        sa.Column("units", sa.Integer, nullable=True),
        sa.Column("revenue", MONEY, nullable=True),
        sa.Column("conversion_rate", sa.Numeric(7, 4), nullable=True),
        sa.Column("aov", MONEY, nullable=True),
        sa.Column("computed_at", TS, nullable=False, server_default=NOW),
    )

    # ---- sync_state --------------------------------------------------------
    op.create_table(
        "sync_state",
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), primary_key=True),
        sa.Column("resource", sa.Text, primary_key=True),
        sa.Column("last_completed_at", TS, nullable=True),
        sa.Column("last_cursor", sa.Text, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_error_at", TS, nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
    )

    # ---- webhook_events_log (TR-14) ---------------------------------------
    op.create_table(
        "webhook_events_log",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("shopify_webhook_id", sa.Text, nullable=True),
        sa.Column("received_at", TS, nullable=False, server_default=NOW),
        sa.Column("hmac_valid", sa.Boolean, nullable=False),
        sa.Column(
            "payload_compressed", sa.LargeBinary, nullable=False
        ),  # raw body, gzip-compressed
        sa.Column("payload_size", sa.Integer, nullable=False),
        sa.Column(
            "processing_status", sa.Text, nullable=False, server_default=sa.text("'received'")
        ),
        sa.Column("processed_at", TS, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_webhook_events_store_received", "webhook_events_log", ["store_id", "received_at"]
    )
    op.create_index(
        "ix_webhook_events_topic_received", "webhook_events_log", ["topic", "received_at"]
    )

    # ---- api_audit_log (TR-6) ---------------------------------------------
    op.create_table(
        "api_audit_log",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("ts", TS, nullable=False, server_default=NOW),
        sa.Column("caller_identity", sa.Text, nullable=False),
        sa.Column("store_id", sa.BigInteger, sa.ForeignKey("stores.id"), nullable=True),
        sa.Column("surface", sa.Text, nullable=False),  # "rest" | "graphql" | "mcp"
        sa.Column("route_or_tool", sa.Text, nullable=False),
        sa.Column("params_sanitized", postgresql.JSONB, nullable=True),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("request_id", sa.Text, nullable=True),
    )
    op.create_index("ix_api_audit_ts", "api_audit_log", ["ts"])
    op.create_index("ix_api_audit_caller_ts", "api_audit_log", ["caller_identity", "ts"])
    op.create_index("ix_api_audit_store_ts", "api_audit_log", ["store_id", "ts"])


def downgrade() -> None:
    # Reverse dependency order.
    op.drop_table("api_audit_log")
    op.drop_table("webhook_events_log")
    op.drop_table("sync_state")
    op.drop_table("analytics_kpi_daily")
    op.drop_table("sessions_daily")
    op.drop_table("subscription_contracts")
    op.drop_table("fulfillments")
    op.drop_table("order_shipping_addresses")
    op.drop_table("order_line_items")
    op.drop_table("orders")
    op.drop_table("inventory_levels")
    op.drop_table("inventory_items")
    op.drop_table("variants")
    op.drop_table("products")
    op.drop_table("customers")
    op.drop_table("locations")
    op.drop_table("stores")
