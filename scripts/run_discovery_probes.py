#!/usr/bin/env python3
"""Phase 0 discovery probes for the Shopify Multi-Store Connector.

For each of the three configured stores, this script:

    1. POSTs the Dev-Dashboard-issued client_id + client_secret to
       https://{shop}.myshopify.com/admin/oauth/access_token with
       grant_type=client_credentials and gets back a 24h access token.
       (Required since 2026-01-01 --- the legacy `shpat_` static-token
       custom-app flow has been removed; see docs/custom_app_setup.md.)
    2. Uses that token to run the discovery probes from docs/discovery.md:
        - shop plan (confirms shopifyPlus flag matches what we recorded)
        - locations connection (count + names + active count)
        - ordersCount over the last 30 days (volume profile)
        - productsCount and productVariantsCount
        - ShopifyQL `sessions` probe (TR-11 pass/fail per store)

Usage (from project root):

    python scripts/run_discovery_probes.py

Reads from .env (in the project root):
    SHOPIFY_<STORE>_SHOP
    SHOPIFY_<STORE>_CLIENT_ID
    SHOPIFY_<STORE>_CLIENT_SECRET

Stores with missing or placeholder credentials are skipped with a clear note.

Stdlib only --- intentionally no httpx/gql/dotenv dependency, so this can
run before the project's Python environment is installed.

Output is markdown. Pipe to a file if you want to commit it:

    python scripts/run_discovery_probes.py > docs/probe_results.md
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API_VERSION = "2026-04"

# Slug used in env-var names for each store.
STORES: list[str] = ["LUBELIFE", "SHOPJO", "SHOPSHIBARI"]


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

def parse_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser: ignores comments / blank lines, strips quotes."""
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
    """Merge .env (project root) under process env (process env wins)."""
    env = parse_dotenv(PROJECT_ROOT / ".env")
    env.update(os.environ)
    return env


# ---------------------------------------------------------------------------
# OAuth client credentials grant
# ---------------------------------------------------------------------------

def oauth_token_exchange(
    shop_domain: str, client_id: str, client_secret: str
) -> dict[str, Any]:
    """POST to /admin/oauth/access_token with grant_type=client_credentials.

    On success returns {"access_token": "...", "scope": "...", "expires_in": 86399}.
    On failure returns a dict with `_http_error` or `_url_error`.
    """
    url = f"https://{shop_domain}/admin/oauth/access_token"
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
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
    except urllib.error.URLError as exc:
        return {"_url_error": str(exc.reason)}


# ---------------------------------------------------------------------------
# GraphQL transport
# ---------------------------------------------------------------------------

def graphql(
    shop_domain: str,
    token: str,
    query: str,
    variables: dict[str, Any] | None = None,
    api_version: str = DEFAULT_API_VERSION,
) -> dict[str, Any]:
    url = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
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
    except urllib.error.URLError as exc:
        return {"_url_error": str(exc.reason)}


# ---------------------------------------------------------------------------
# Probe queries
# ---------------------------------------------------------------------------

SHOP_PLAN_QUERY = """
{
  shop {
    name
    primaryDomain { url }
    plan { displayName partnerDevelopment shopifyPlus }
  }
}
"""

LOCATIONS_QUERY = """
{
  locations(first: 50) {
    edges {
      node {
        id
        legacyResourceId
        name
        isActive
        fulfillsOnlineOrders
        shipsInventory
        address {
          address1
          city
          province
          zip
          country
        }
      }
    }
  }
}
"""

ORDERS_COUNT_QUERY = """
query OrdersCount($q: String!) {
  ordersCount(query: $q) { count }
}
"""

PRODUCTS_COUNT_QUERY = "{ productsCount { count } }"

VARIANTS_COUNT_QUERY = "{ productVariantsCount { count } }"

SHOPIFYQL_SESSIONS_QUERY = """
{
  shopifyqlQuery(query: "FROM sales, sessions SHOW day, total_sales, orders, sessions GROUP BY day SINCE -7d UNTIL -1d") {
    parseErrors
    tableData {
      columns { name dataType displayName subType }
      rows
    }
  }
}
"""


def thirty_days_ago_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Probe runner
# ---------------------------------------------------------------------------

def safe_get(payload: dict[str, Any], *path: str) -> Any:
    cur: Any = payload
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def run_probes_for_store(
    shop_domain: str, token: str, api_version: str
) -> dict[str, Any]:
    out: dict[str, Any] = {"shop": shop_domain, "api_version": api_version}

    plan_payload = graphql(shop_domain, token, SHOP_PLAN_QUERY, api_version=api_version)
    out["plan_raw"] = plan_payload
    plan = safe_get(plan_payload, "data", "shop", "plan")
    if plan:
        out["plan"] = plan
    else:
        out["plan"] = {"_error": True}

    locs_payload = graphql(shop_domain, token, LOCATIONS_QUERY, api_version=api_version)
    edges = safe_get(locs_payload, "data", "locations", "edges")
    if edges is None:
        out["locations"] = {"_error": True, "raw": locs_payload}
    else:
        nodes = [e["node"] for e in edges]
        # Group by normalized address to surface duplicate physical locations.
        addr_groups: dict[str, list[str]] = {}
        for n in nodes:
            a = n.get("address") or {}
            key = "|".join(
                str(a.get(k) or "").strip().lower()
                for k in ("address1", "city", "province", "zip", "country")
            )
            addr_groups.setdefault(key, []).append(n["name"])
        duplicate_groups = [grp for grp in addr_groups.values() if len(grp) > 1]
        out["locations"] = {
            "count": len(nodes),
            "active_count": sum(1 for n in nodes if n["isActive"]),
            "fulfills_online_count": sum(1 for n in nodes if n.get("fulfillsOnlineOrders")),
            "ships_inventory_count": sum(1 for n in nodes if n.get("shipsInventory")),
            "details": [
                {
                    "name": n["name"],
                    "id": n["id"],
                    "legacy_id": n.get("legacyResourceId"),
                    "active": n["isActive"],
                    "fulfills_online": n.get("fulfillsOnlineOrders"),
                    "ships_inventory": n.get("shipsInventory"),
                    "address": n.get("address") or {},
                }
                for n in nodes
            ],
            "duplicate_address_groups": duplicate_groups,
        }

    orders_payload = graphql(
        shop_domain,
        token,
        ORDERS_COUNT_QUERY,
        variables={"q": f"created_at:>={thirty_days_ago_iso()}"},
        api_version=api_version,
    )
    orders_count = safe_get(orders_payload, "data", "ordersCount", "count")
    out["orders_30d"] = (
        {"count": orders_count}
        if orders_count is not None
        else {"_error": True, "raw": orders_payload}
    )

    products_payload = graphql(
        shop_domain, token, PRODUCTS_COUNT_QUERY, api_version=api_version
    )
    products_count = safe_get(products_payload, "data", "productsCount", "count")
    out["products"] = (
        {"count": products_count}
        if products_count is not None
        else {"_error": True, "raw": products_payload}
    )

    variants_payload = graphql(
        shop_domain, token, VARIANTS_COUNT_QUERY, api_version=api_version
    )
    variants_count = safe_get(variants_payload, "data", "productVariantsCount", "count")
    if variants_count is not None:
        out["variants"] = {"count": variants_count}
    elif "errors" in variants_payload:
        out["variants"] = {
            "_field_unavailable": True,
            "note": "productVariantsCount not exposed in this API version",
        }
    else:
        out["variants"] = {"_error": True, "raw": variants_payload}

    sql_payload = graphql(
        shop_domain, token, SHOPIFYQL_SESSIONS_QUERY, api_version=api_version
    )
    # Top-level GraphQL errors (auth, scope, schema mismatches) come back here.
    if sql_payload.get("errors"):
        msgs = [e.get("message", "") for e in sql_payload["errors"]]
        out["shopifyql_sessions"] = {
            "pass": False,
            "graphql_errors": msgs,
            "scope_issue": any("read_reports" in m or "ACCESS_DENIED" in m for m in msgs),
        }
    else:
        node = safe_get(sql_payload, "data", "shopifyqlQuery")
        parse_errors = node.get("parseErrors") if node else None
        if not node:
            out["shopifyql_sessions"] = {"pass": False, "raw": sql_payload}
        elif parse_errors:
            out["shopifyql_sessions"] = {
                "pass": False,
                "parse_errors": parse_errors,
            }
        else:
            table = node.get("tableData") or {}
            rows = table.get("rows") or []
            cols = [c["name"] for c in (table.get("columns") or [])]
            out["shopifyql_sessions"] = {
                "pass": bool(rows),
                "row_count": len(rows),
                "columns": cols,
                "sessions_column_present": "sessions" in cols,
            }

    return out


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_store(slug: str, results: dict[str, Any]) -> str:
    L = [f"### {slug.lower()}.com  --  `{results['shop']}`", ""]
    L.append("| Probe | Result |")
    L.append("|---|---|")

    plan = results["plan"]
    if "_error" in plan:
        L.append("| Plan | **ERROR** (see plan_raw in JSON) |")
    else:
        L.append(f"| Plan name | `{plan.get('displayName')}` |")
        L.append(f"| shopifyPlus flag | `{plan.get('shopifyPlus')}` |")
        L.append(f"| partnerDevelopment | `{plan.get('partnerDevelopment')}` |")

    locs = results["locations"]
    if "_error" in locs:
        L.append("| Locations | **ERROR** |")
    else:
        L.append(
            f"| Locations (total / active / fulfills-online / ships-inventory) | "
            f"{locs['count']} / {locs['active_count']} / "
            f"{locs.get('fulfills_online_count', '?')} / "
            f"{locs.get('ships_inventory_count', '?')} |"
        )
        for d in locs.get("details", []):
            a = d["address"]
            addr_str = ", ".join(
                str(v) for v in (a.get("address1"), a.get("city"), a.get("province"), a.get("zip")) if v
            ) or "_no address_"
            flags = []
            if d.get("fulfills_online"):
                flags.append("online")
            if d.get("ships_inventory"):
                flags.append("ships")
            if not d.get("active"):
                flags.append("INACTIVE")
            flags_str = f" [{', '.join(flags)}]" if flags else ""
            L.append(f"| Location | `{d['name']}`{flags_str} --- {addr_str} |")
        if locs.get("duplicate_address_groups"):
            for grp in locs["duplicate_address_groups"]:
                L.append(
                    f"| **Duplicate address** | These share an address: "
                    f"{', '.join(f'`{n}`' for n in grp)} |"
                )

    orders = results["orders_30d"]
    L.append(
        f"| Orders (last 30d) | {orders.get('count', '**ERROR**')} |"
    )

    prods = results["products"]
    L.append(f"| Products | {prods.get('count', '**ERROR**')} |")

    variants = results["variants"]
    if "count" in variants:
        L.append(f"| Variants | {variants['count']} |")
    elif "_field_unavailable" in variants:
        L.append("| Variants | _field unavailable in this API version_ |")
    else:
        L.append("| Variants | **ERROR** |")

    sql = results["shopifyql_sessions"]
    status = "**PASS**" if sql.get("pass") else "**FAIL**"
    detail = ""
    if sql.get("scope_issue"):
        detail = " - **scope issue** (likely missing `read_reports` and/or Level 2 customer data access)"
    elif "graphql_errors" in sql:
        detail = f" - GraphQL errors: `{sql['graphql_errors'][:1]}`"
    elif "parse_errors" in sql:
        detail = f" - parse errors: `{sql['parse_errors']}`"
    elif "row_count" in sql:
        detail = (
            f" - rows: {sql['row_count']}, columns: {sql.get('columns')}, "
            f"sessions column: {sql.get('sessions_column_present')}"
        )
    L.append(f"| ShopifyQL `sessions` probe (TR-11) | {status}{detail} |")

    L.append("")
    return "\n".join(L)


def render_summary_table(per_store: list[tuple[str, dict[str, Any]]]) -> str:
    L = ["### Summary", ""]
    L.append("| Store | Plus | Locations | Orders/30d | Products | Variants | SQL probe |")
    L.append("|---|---|---|---|---|---|---|")
    for slug, r in per_store:
        plan = r["plan"]
        plus = plan.get("shopifyPlus") if "_error" not in plan else "ERR"
        loc = r["locations"].get("count", "ERR")
        orders = r["orders_30d"].get("count", "ERR")
        prods = r["products"].get("count", "ERR")
        if "count" in r["variants"]:
            variants = r["variants"]["count"]
        elif "_field_unavailable" in r["variants"]:
            variants = "n/a"
        else:
            variants = "ERR"
        sql_pass = "PASS" if r["shopifyql_sessions"].get("pass") else "FAIL"
        L.append(
            f"| {slug.lower()} | {plus} | {loc} | {orders} | {prods} | {variants} | {sql_pass} |"
        )
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    env = load_env()
    api_version = env.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)

    print(f"# Phase 0 Probe Results")
    print()
    print(f"_Shopify Admin API version: `{api_version}` -- generated "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    print()

    per_store: list[tuple[str, dict[str, Any]]] = []
    for slug in STORES:
        shop = env.get(f"SHOPIFY_{slug}_SHOP", "")
        client_id = env.get(f"SHOPIFY_{slug}_CLIENT_ID", "")
        client_secret = env.get(f"SHOPIFY_{slug}_CLIENT_SECRET", "")

        creds_missing = (
            not shop
            or not client_id
            or not client_secret
            or client_id.startswith("replace-with")
            or client_secret.startswith("replace-with")
        )
        if creds_missing:
            print(f"### {slug.lower()}.com")
            print()
            print("_Skipped: missing or placeholder `client_id` / `client_secret` in `.env`._")
            print()
            continue

        print(f"<!-- exchanging client credentials for {shop} ... -->", file=sys.stderr)
        token_resp = oauth_token_exchange(shop, client_id, client_secret)
        if "access_token" not in token_resp:
            print(f"### {slug.lower()}.com  --  `{shop}`")
            print()
            print("_OAuth token exchange failed --- credentials rejected by Shopify._")
            print()
            print("```json")
            print(json.dumps(token_resp, indent=2))
            print("```")
            print()
            continue
        token = token_resp["access_token"]

        print(f"<!-- probing {shop} ... -->", file=sys.stderr)
        results = run_probes_for_store(shop, token, api_version)
        results["oauth"] = {
            "scope": token_resp.get("scope"),
            "expires_in": token_resp.get("expires_in"),
        }
        per_store.append((slug, results))
        print(render_store(slug, results))

    if per_store:
        print(render_summary_table(per_store))

    if not per_store:
        print("_No stores probed. Fill in tokens in `.env` and re-run._")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
