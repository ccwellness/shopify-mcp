"""Bulk JSONL parser/grouper.

Shopify's bulk operations emit one JSON object per line. Top-level entities
appear as their own lines; nested children get a `__parentId` field pointing
back to the parent's GID. Children of one parent are grouped together but
order between siblings isn't guaranteed.

This grouper reads everything into memory (fine for v1's volume) and
attaches each child to its parent's appropriate list, picked from a
caller-supplied `child_type_to_field` map keyed on the GID type prefix
(e.g. `"LineItem"` from `gid://shopify/LineItem/...`).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


def _gid_type(gid: str | None) -> str | None:
    """Extract the type segment from a Shopify GID, e.g. 'gid://shopify/Order/1' -> 'Order'."""
    if not gid or not gid.startswith("gid://shopify/"):
        return None
    parts = gid.split("/")
    return parts[3] if len(parts) >= 4 else None  # noqa: PLR2004


def group_bulk_jsonl(
    lines: Iterable[bytes],
    child_type_to_field: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """Read bulk JSONL and return root_gid -> root_object with children attached.

    Roots are JSON objects WITHOUT a `__parentId` field. Children have one;
    they're attached onto `root[child_type_to_field[child_type]]` as a list.
    Children whose type isn't in the map are dropped (with no error).
    """
    roots: dict[str, dict[str, Any]] = {}
    children_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for raw in lines:
        if not raw or not raw.strip():
            continue
        obj: dict[str, Any] = json.loads(raw)
        parent_id = obj.pop("__parentId", None)
        if parent_id is None:
            obj_id = obj.get("id")
            if obj_id is None:
                continue
            roots[str(obj_id)] = obj
        else:
            children_by_parent[str(parent_id)].append(obj)

    for parent_id, children in children_by_parent.items():
        parent = roots.get(parent_id)
        if parent is None:
            continue
        for child in children:
            child_type = _gid_type(child.get("id"))
            if child_type is None:
                continue
            field = child_type_to_field.get(child_type)
            if field is None:
                continue
            parent.setdefault(field, []).append(child)

    return roots
