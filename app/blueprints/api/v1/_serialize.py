"""JSON serialization helpers for domain dataclasses.

Money fields go out as strings (Decimal('21.98') → '21.98') so JSON
clients don't lose precision. Datetimes use ISO 8601 with offset.
StrEnum values serialize to their string value, so callers see
'paid' rather than 'FinancialStatus.PAID'.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.models import (
    AnalyticsKpiDay,
    Fulfillment,
    InventoryLevel,
    Order,
    OrderLineItem,
    OrderShippingAddress,
    StoreComparison,
    StoreComparisonRow,
)


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _line_item(li: OrderLineItem) -> dict[str, Any]:
    return {
        "id": int(li.id),
        "store_id": int(li.store_id),
        "variant_id": int(li.variant_id) if li.variant_id is not None else None,
        "product_id": int(li.product_id) if li.product_id is not None else None,
        "gid": li.gid,
        "title": li.title,
        "sku": li.sku,
        "vendor": li.vendor,
        "quantity": li.quantity,
        "price": _money(li.price),
        "total_discount": _money(li.total_discount),
        "fulfillment_status": (
            str(li.fulfillment_status) if li.fulfillment_status is not None else None
        ),
        "requires_shipping": li.requires_shipping,
        "taxable": li.taxable,
    }


def _shipping(addr: OrderShippingAddress | None) -> dict[str, Any] | None:
    if addr is None:
        return None
    return {
        "name": addr.name,
        "company": addr.company,
        "address1": addr.address1,
        "address2": addr.address2,
        "city": addr.city,
        "province": addr.province,
        "country": addr.country,
        "zip": addr.zip,
        "phone": addr.phone,
        "latitude": str(addr.latitude) if addr.latitude is not None else None,
        "longitude": str(addr.longitude) if addr.longitude is not None else None,
    }


def _fulfillment(f: Fulfillment) -> dict[str, Any]:
    return {
        "id": int(f.id),
        "location_id": int(f.location_id) if f.location_id is not None else None,
        "gid": f.gid,
        "status": str(f.status),
        "shipment_status": str(f.shipment_status) if f.shipment_status is not None else None,
        "tracking_company": f.tracking_company,
        "tracking_number": f.tracking_number,
        "tracking_url": f.tracking_url,
        "created_at": _dt(f.created_at),
        "updated_at": _dt(f.updated_at),
    }


def inventory_level_to_json(level: InventoryLevel) -> dict[str, Any]:
    return {
        "id": int(level.id),
        "store_id": int(level.store_id),
        "inventory_item_id": int(level.inventory_item_id),
        "location_id": int(level.location_id),
        "available": level.available,
        "on_hand": level.on_hand,
        "committed": level.committed,
        "incoming": level.incoming,
        "updated_at": _dt(level.updated_at),
    }


def _comparison_row(row: StoreComparisonRow) -> dict[str, Any]:
    return {
        "store_id": int(row.store_id),
        "store_key": row.store_key,
        "order_count": row.order_count,
        "paid_revenue": _money(row.paid_revenue),
        "refunds_total": _money(row.refunds_total),
        "net_revenue": _money(row.net_revenue),
        "units_sold": row.units_sold,
        "currency_code": row.currency_code,
        "status_counts": {str(status): count for status, count in row.status_counts.items()},
    }


def store_comparison_to_json(comparison: StoreComparison) -> dict[str, Any]:
    return {
        "since": _dt(comparison.since),
        "until": _dt(comparison.until),
        "currency_warning": comparison.currency_warning,
        "rows": [_comparison_row(r) for r in comparison.rows],
    }


def kpi_day_to_json(row: AnalyticsKpiDay) -> dict[str, Any]:
    return {
        "store_id": int(row.store_id),
        "date": row.date.isoformat(),
        "sessions": row.sessions,
        "orders": row.orders,
        "units": row.units,
        "revenue": _money(row.revenue),
        # conversion_rate is Numeric(7,4) — surface as string for parity w/ Money.
        "conversion_rate": str(row.conversion_rate) if row.conversion_rate is not None else None,
        "aov": _money(row.aov),
        "computed_at": _dt(row.computed_at),
    }


def order_to_json(order: Order) -> dict[str, Any]:
    return {
        "id": int(order.id),
        "store_id": int(order.store_id),
        "customer_id": int(order.customer_id) if order.customer_id is not None else None,
        "gid": order.gid,
        "legacy_id": order.legacy_id,
        "name": order.name,
        "order_number": order.order_number,
        "email": order.email,
        "financial_status": (
            str(order.financial_status) if order.financial_status is not None else None
        ),
        "fulfillment_status": (
            str(order.fulfillment_status) if order.fulfillment_status is not None else None
        ),
        "currency_code": order.currency_code,
        "presentment_currency_code": order.presentment_currency_code,
        "subtotal_price": _money(order.subtotal_price),
        "total_price": _money(order.total_price),
        "total_tax": _money(order.total_tax),
        "total_discounts": _money(order.total_discounts),
        "total_shipping": _money(order.total_shipping),
        "presentment_subtotal_price": _money(order.presentment_subtotal_price),
        "presentment_total_price": _money(order.presentment_total_price),
        "processed_at": _dt(order.processed_at),
        "cancelled_at": _dt(order.cancelled_at),
        "closed_at": _dt(order.closed_at),
        "created_at": _dt(order.created_at),
        "updated_at": _dt(order.updated_at),
        "line_items": [_line_item(li) for li in order.line_items],
        "shipping_address": _shipping(order.shipping_address),
        "fulfillments": [_fulfillment(f) for f in order.fulfillments],
    }
