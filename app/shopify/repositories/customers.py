"""Live CustomerRepository — reads customers from Shopify GraphQL."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.models import Customer, CustomerId, StoreId
from app.shopify.live_paging import gid_tail
from app.shopify.repositories._base import _LiveRepo, customer_gid

_CUSTOMER_NODE = """
  id legacyResourceId email phone firstName lastName
  numberOfOrders createdAt updatedAt
  amountSpent { amount currencyCode }
"""

_GET_CUSTOMER = f"query LiveCustomer($id: ID!) {{ customer(id: $id) {{ {_CUSTOMER_NODE} }} }}"

_FIND_CUSTOMER = f"""
query LiveFindCustomer($query: String!, $first: Int!) {{
  customers(first: $first, query: $query) {{
    edges {{ node {{ {_CUSTOMER_NODE} }} }}
  }}
}}
"""


def _ts(value: Any) -> datetime:
    return datetime.fromisoformat(str(value))


class LiveCustomerRepository(_LiveRepo):
    def _to_customer(self, store_id: StoreId, node: dict[str, Any]) -> Customer:
        spent = node.get("amountSpent") or {}
        amount = spent.get("amount")
        return Customer(
            id=CustomerId(gid_tail(node.get("id")) or int(node["legacyResourceId"])),
            store_id=store_id,
            gid=str(node["id"]),
            legacy_id=int(node["legacyResourceId"]),
            email=node.get("email"),
            phone=node.get("phone"),
            first_name=node.get("firstName"),
            last_name=node.get("lastName"),
            accepts_marketing=False,
            orders_count=int(node.get("numberOfOrders") or 0),
            total_spent=Decimal(str(amount)) if amount is not None else Decimal("0"),
            currency_code=spent.get("currencyCode"),
            created_at=_ts(node.get("createdAt")),
            updated_at=_ts(node.get("updatedAt")),
        )

    def get(self, customer_id: CustomerId) -> Customer | None:
        for store_id in self._index.all_store_ids():
            found = self.get_by_gid(store_id, customer_gid(int(customer_id)))
            if found is not None:
                return found
        return None

    def get_by_gid(self, store_id: StoreId, gid: str) -> Customer | None:
        key = self._key(store_id)
        if key is None:
            return None
        data = self._query(key, _GET_CUSTOMER, {"id": gid})
        node = data.get("customer")
        return self._to_customer(store_id, node) if node else None

    def get_by_email(self, store_id: StoreId, email: str) -> Customer | None:
        key = self._key(store_id)
        if key is None:
            return None
        safe = email.replace('"', "")
        data = self._query(key, _FIND_CUSTOMER, {"query": f'email:"{safe}"', "first": 1})
        edges = (data.get("customers") or {}).get("edges") or []
        for edge in edges:
            node = (edge or {}).get("node")
            if node:
                return self._to_customer(store_id, node)
        return None

    def legacy_id_map(self, store_id: StoreId) -> dict[int, CustomerId]:
        raise NotImplementedError("legacy_id_map is a sync-only method")

    def upsert(self, customer: Customer) -> None:  # noqa: ARG002
        return None
