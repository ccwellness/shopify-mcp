"""Unit tests for `normalize_order_bulk`.

The bulk normalizer was previously dropping order-level discount
allocations on the floor (it only read `totalDiscountSet`, missing
`discountAllocations`). It also wasn't capturing `sourceName`, so
the dashboard had no way to flag draft orders. These tests lock in
both behaviors against the GraphQL shape we get back from Shopify.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domain.models import StoreId
from app.shopify.normalizers.orders_bulk import normalize_order_bulk

LUBELIFE = StoreId(5)


def _base_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "gid://shopify/Order/7117040550127",
        "legacyResourceId": "7117040550127",
        "name": "#42220",
        "processedAt": "2026-05-13T15:59:42Z",
        "createdAt": "2026-05-13T16:11:01Z",
        "updatedAt": "2026-05-13T16:11:01Z",
        "currencyCode": "USD",
        "displayFinancialStatus": "PAID",
        "sourceName": "web",
        "subtotalPriceSet": {"shopMoney": {"amount": "98.97", "currencyCode": "USD"}},
        "totalPriceSet": {"shopMoney": {"amount": "0.00", "currencyCode": "USD"}},
        "totalTaxSet": {"shopMoney": {"amount": "0.00", "currencyCode": "USD"}},
        "totalDiscountsSet": {"shopMoney": {"amount": "98.97", "currencyCode": "USD"}},
        "totalShippingPriceSet": {"shopMoney": {"amount": "0.00", "currencyCode": "USD"}},
        "line_items": [],
    }
    payload.update(overrides)
    return payload


def _line_payload(**overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "id": "gid://shopify/LineItem/123",
        "title": "Water-Based Lubricant",
        "sku": "40797",
        "quantity": 3,
        "originalUnitPriceSet": {"shopMoney": {"amount": "32.99"}},
        "totalDiscountSet": {"shopMoney": {"amount": "0.00"}},
        "discountAllocations": [],
        "requiresShipping": True,
        "taxable": True,
    }
    line.update(overrides)
    return line


# ---------------------------------------------------------------------------
# source_name capture
# ---------------------------------------------------------------------------


def test_source_name_web_for_normal_order() -> None:
    result = normalize_order_bulk(LUBELIFE, _base_payload(sourceName="web"))
    assert result.order.source_name == "web"


def test_source_name_draft_for_admin_created_order() -> None:
    result = normalize_order_bulk(LUBELIFE, _base_payload(sourceName="shopify_draft_order"))
    assert result.order.source_name == "shopify_draft_order"


def test_source_name_is_none_when_missing() -> None:
    payload = _base_payload()
    payload.pop("sourceName", None)
    result = normalize_order_bulk(LUBELIFE, payload)
    assert result.order.source_name is None


# ---------------------------------------------------------------------------
# Line-item discount allocations
# ---------------------------------------------------------------------------


def test_line_total_discount_includes_order_level_allocations() -> None:
    """The shape Shopify returned for order #42220 — full $98.97 manual
    discount lives in `discountAllocations`, NOT `totalDiscountSet`."""
    line = _line_payload(
        totalDiscountSet={"shopMoney": {"amount": "0.00"}},
        discountAllocations=[
            {"allocatedAmountSet": {"shopMoney": {"amount": "98.97"}}},
        ],
    )
    result = normalize_order_bulk(LUBELIFE, _base_payload(line_items=[line]))
    expected = Decimal("98.97")
    assert result.order.line_items[0].total_discount == expected


def test_line_total_discount_sums_per_unit_and_order_level_discounts() -> None:
    """When both shapes carry value, they're disjoint and we sum them."""
    line = _line_payload(
        totalDiscountSet={"shopMoney": {"amount": "5.00"}},
        discountAllocations=[
            {"allocatedAmountSet": {"shopMoney": {"amount": "10.00"}}},
            {"allocatedAmountSet": {"shopMoney": {"amount": "2.50"}}},
        ],
    )
    result = normalize_order_bulk(LUBELIFE, _base_payload(line_items=[line]))
    expected = Decimal("17.50")
    assert result.order.line_items[0].total_discount == expected


def test_line_total_discount_zero_when_no_allocations_and_no_line_discount() -> None:
    line = _line_payload(
        totalDiscountSet={"shopMoney": {"amount": "0.00"}},
        discountAllocations=[],
    )
    result = normalize_order_bulk(LUBELIFE, _base_payload(line_items=[line]))
    assert result.order.line_items[0].total_discount == Decimal("0")


def test_line_total_discount_falls_back_when_allocations_missing() -> None:
    """Some orders don't even have the `discountAllocations` key."""
    line = _line_payload(totalDiscountSet={"shopMoney": {"amount": "7.50"}})
    line.pop("discountAllocations", None)
    result = normalize_order_bulk(LUBELIFE, _base_payload(line_items=[line]))
    expected = Decimal("7.50")
    assert result.order.line_items[0].total_discount == expected
