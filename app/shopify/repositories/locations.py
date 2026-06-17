"""Live LocationRepository — reads locations from Shopify GraphQL."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.models import Location, LocationId, StoreId
from app.shopify.live_paging import iter_all_nodes
from app.shopify.repositories._base import _LiveRepo

_LOCATION_NODE = """
  id legacyResourceId name isActive fulfillsOnlineOrders shipsInventory
  address { address1 address2 city province zip country }
"""

_LIST_LOCATIONS = f"""
query LiveLocations($first: Int!, $after: String) {{
  locations(first: $first, after: $after) {{
    edges {{ node {{ {_LOCATION_NODE} }} }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

_GET_LOCATION = f"query LiveLocation($id: ID!) {{ location(id: $id) {{ {_LOCATION_NODE} }} }}"


class LiveLocationRepository(_LiveRepo):
    def _to_location(self, store_id: StoreId, node: dict[str, Any]) -> Location:
        addr = node.get("address") or {}
        return Location(
            id=LocationId(int(node["legacyResourceId"])),
            store_id=store_id,
            gid=str(node["id"]),
            legacy_id=int(node["legacyResourceId"]),
            name=str(node.get("name") or ""),
            address1=addr.get("address1"),
            address2=addr.get("address2"),
            city=addr.get("city"),
            province=addr.get("province"),
            postal_code=addr.get("zip"),
            country=addr.get("country"),
            is_active=bool(node.get("isActive", True)),
            fulfills_online_orders=bool(node.get("fulfillsOnlineOrders", False)),
            ships_inventory=bool(node.get("shipsInventory", False)),
            last_seen_at=datetime.now(tz=UTC),
        )

    def list_for_store(self, store_id: StoreId) -> tuple[Location, ...]:
        key = self._key(store_id)
        if key is None:
            return ()
        nodes = iter_all_nodes(
            lambda after: self._query(key, _LIST_LOCATIONS, {"first": 100, "after": after}).get(
                "locations"
            )
        )
        return tuple(self._to_location(store_id, n) for n in nodes)

    def get(self, location_id: LocationId) -> Location | None:
        gid = f"gid://shopify/Location/{int(location_id)}"
        for store_id in self._index.all_store_ids():
            found = self.get_by_gid(store_id, gid)
            if found is not None:
                return found
        return None

    def get_by_gid(self, store_id: StoreId, gid: str) -> Location | None:
        key = self._key(store_id)
        if key is None:
            return None
        data = self._query(key, _GET_LOCATION, {"id": gid})
        node = data.get("location")
        return self._to_location(store_id, node) if node else None

    def upsert(self, location: Location) -> None:  # noqa: ARG002
        return None
