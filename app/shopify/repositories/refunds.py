"""Live RefundRepository — reads refunds from Shopify GraphQL.

Refunds have no global list endpoint, so window queries lean on the fact that
creating a refund bumps the order's `updated_at`: every refund created on/after
`since` lives on an order with `updated_at >= since`. We page those orders and
keep refunds whose `createdAt` falls in the window.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.domain.models import Money, OrderId, Refund, RefundId, StoreId
from app.shopify.live_paging import gid_tail, iter_all_nodes
from app.shopify.normalizers.refunds import normalize_refund_payload
from app.shopify.repositories._base import _LiveRepo, order_gid

_REFUND_FIELDS = """
  id legacyResourceId note createdAt
  totalRefundedSet { shopMoney { amount currencyCode } }
"""

_ORDER_REFUNDS = f"""
query LiveOrderRefunds($id: ID!) {{
  order(id: $id) {{ id legacyResourceId refunds {{ {_REFUND_FIELDS} }} }}
}}
"""

_WINDOW_REFUNDS = f"""
query LiveWindowRefunds($query: String, $first: Int!, $after: String) {{
  orders(first: $first, after: $after, query: $query,
         sortKey: UPDATED_AT, reverse: true) {{
    edges {{ node {{ id legacyResourceId refunds {{ {_REFUND_FIELDS} }} }} }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class LiveRefundRepository(_LiveRepo):
    def get_by_gid(self, store_id: StoreId, gid: str) -> Refund | None:  # noqa: ARG002
        # Not reachable from the MCP read tools; refunds are listed per order.
        return None

    def list_for_order(self, order_id: OrderId) -> tuple[Refund, ...]:
        for store_id in self._index.all_store_ids():
            key = self._key(store_id)
            if key is None:
                continue
            data = self._query(key, _ORDER_REFUNDS, {"id": order_gid(int(order_id))})
            order = data.get("order")
            if not order:
                continue
            return tuple(
                normalize_refund_payload(store_id, order_id, r)
                for r in (order.get("refunds") or [])
                if isinstance(r, dict)
            )
        return ()

    def list_in_window(
        self, store_id: StoreId, since: datetime, until: datetime
    ) -> tuple[Refund, ...]:
        key = self._key(store_id)
        if key is None:
            return ()
        query = f"updated_at:>={_iso_z(since)}"

        def _run(after: str | None) -> dict[str, Any] | None:
            data = self._query(key, _WINDOW_REFUNDS, {"query": query, "first": 100, "after": after})
            return data.get("orders")

        out: list[Refund] = []
        for node in iter_all_nodes(_run):
            order_legacy = gid_tail(node.get("id")) or 0
            for raw in node.get("refunds") or []:
                if not isinstance(raw, dict):
                    continue
                refund = normalize_refund_payload(store_id, OrderId(order_legacy), raw)
                if since <= refund.created_at < until:
                    out.append(refund)
        return tuple(out)

    def sum_in_window(self, store_id: StoreId, since: datetime, until: datetime) -> Money:
        total = Decimal("0")
        for refund in self.list_in_window(store_id, since, until):
            total += refund.amount
        return total

    def upsert(self, refund: Refund) -> RefundId:
        return RefundId(refund.legacy_id)
