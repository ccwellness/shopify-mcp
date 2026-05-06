#!/usr/bin/env python3
"""One-off debugger for the ShopifyQL TR-11 sessions probe failure.

Runs several query variations against lubelife and prints the raw response
for each, so we can isolate whether the failure is query-shape, scope,
plan-gating, or a deprecated field.

Usage (from project root):

    python scripts/debug_shopifyql.py

Reads SHOPIFY_LUBELIFE_SHOP / _CLIENT_ID / _CLIENT_SECRET from .env.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API_VERSION = "2026-04"


def parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'").strip('"')
    return env


def load_env() -> dict[str, str]:
    env = parse_dotenv(PROJECT_ROOT / ".env")
    env.update(os.environ)
    return env


def oauth_exchange(shop: str, cid: str, csec: str) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "client_id": cid, "client_secret": csec}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://{shop}/admin/oauth/access_token",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def graphql(shop: str, token: str, query: str, api_version: str) -> dict[str, Any]:
    url = f"https://{shop}/admin/api/{api_version}/graphql.json"
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {
            "_http_error": exc.code,
            "_body": exc.read().decode("utf-8", errors="replace")[:500],
        }


def shopifyql_probe(query_str: str) -> str:
    """Wrap a raw ShopifyQL string into a GraphQL document (NEW schema, 2026-04)."""
    escaped = query_str.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "{ shopifyqlQuery(query: \"" + escaped + "\") { "
        "  parseErrors "
        "  tableData { columns { name dataType displayName subType } rows } "
        "} }"
    )


# Query variations after learning `sessions` is both a dataset name AND a column,
# per Shopify's docs example: `FROM sales, sessions SHOW day, total_sales, sessions GROUP BY day`.
VARIATIONS = [
    # 1. sessions dataset solo, sessions column
    ("sessions-solo",
     "FROM sessions SHOW sessions SINCE -1d UNTIL today"),
    # 2. Joined sales + sessions per Shopify docs example.
    ("docs-example",
     "FROM sales, sessions SHOW day, total_sales, sessions GROUP BY day SINCE -7d UNTIL -1d"),
    # 3. Sales dataset, no sessions --- confirm sales works at all on this plan.
    ("sales-no-sessions",
     "FROM sales SHOW total_sales, orders SINCE -7d UNTIL -1d"),
    # 4. Bare minimum --- confirm ShopifyQL responds for any query.
    ("ping",
     "FROM products SHOW count"),
]


def main() -> int:
    env = load_env()
    shop = env.get("SHOPIFY_LUBELIFE_SHOP", "")
    cid = env.get("SHOPIFY_LUBELIFE_CLIENT_ID", "")
    csec = env.get("SHOPIFY_LUBELIFE_CLIENT_SECRET", "")
    api_version = env.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)

    if not shop or not cid or not csec or cid.startswith("replace-with"):
        print("ERROR: lubelife credentials missing or placeholder in .env", file=sys.stderr)
        return 1

    print(f"Shop:        {shop}")
    print(f"API version: {api_version}")
    print("Exchanging client credentials ...")
    try:
        oauth = oauth_exchange(shop, cid, csec)
    except urllib.error.HTTPError as exc:
        print(f"OAuth FAILED: HTTP {exc.code}\n{exc.read().decode('utf-8', errors='replace')}")
        return 1

    token = oauth.get("access_token")
    if not token:
        print(f"OAuth response missing access_token: {oauth}")
        return 1

    print(f"OAuth scope: {oauth.get('scope')}")
    print(f"Token expires_in: {oauth.get('expires_in')} sec")
    print("=" * 78)

    # ---- Introspection: do the union types still exist? what's the field's return type? ----
    print("\n--- introspection: type existence ---")
    introspect_types = (
        "{ "
        "  tableResponse: __type(name: \"TableResponse\") { name kind } "
        "  parseError: __type(name: \"ParseError\") { name kind } "
        "  shopifyqlResponse: __type(name: \"ShopifyqlResponse\") { name kind } "
        "  shopifyQLResponse: __type(name: \"ShopifyQLResponse\") { name kind } "
        "  tableData: __type(name: \"TableData\") { name kind } "
        "}"
    )
    print(json.dumps(graphql(shop, token, introspect_types, api_version), indent=2)[:1000])

    print("\n--- introspection: shopifyqlQuery field's return type ---")
    field_type = (
        "{ __type(name: \"QueryRoot\") { "
        "  fields { name type { name kind ofType { name kind } } } "
        "} }"
    )
    fields_resp = graphql(shop, token, field_type, api_version)
    fields = ((fields_resp.get("data") or {}).get("__type") or {}).get("fields") or []
    sql_field = next((f for f in fields if "shopifyql" in f["name"].lower()), None)
    print(json.dumps(sql_field, indent=2) if sql_field else "(no field with 'shopifyql' in QueryRoot)")

    print("\n--- introspection: types matching 'shopifyql', 'table', 'tabular', 'sql' ---")
    types_resp = graphql(
        shop, token, "{ __schema { types { name kind } } }", api_version
    )
    types_list = (
        ((types_resp.get("data") or {}).get("__schema") or {}).get("types") or []
    )
    needles = ("shopifyql", "tabular", "tablerow", "tabledata", "tableresponse", "parseerror")
    for t in types_list:
        n = t["name"]
        if any(needle in n.lower() for needle in needles):
            print(f"  {t['kind']:14s} {n}")

    print("\n--- introspection: ShopifyqlQueryResponse fields ---")
    for tname in ("ShopifyqlQueryResponse", "ShopifyqlTableData", "ShopifyqlTableDataColumn"):
        q = (
            "{ __type(name: \"" + tname + "\") { "
            "  name kind "
            "  fields { name type { name kind ofType { name kind ofType { name kind } } } } "
            "} }"
        )
        resp = graphql(shop, token, q, api_version)
        print(f"\n{tname}:")
        print(json.dumps(((resp.get('data') or {}).get('__type') or {}), indent=2)[:1500])

    print("=" * 78)
    for label, qstr in VARIATIONS:
        print(f"\n--- variation: {label} ---")
        print(f"ShopifyQL: {qstr}")
        doc = shopifyql_probe(qstr)
        resp = graphql(shop, token, doc, api_version)
        print(json.dumps(resp, indent=2)[:2000])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
