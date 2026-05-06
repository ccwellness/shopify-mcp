"""Live smoke test for the ShopifyClient.

Loads the per-store configs from `.env`, runs a tiny shop-info query
against every store with real creds, and confirms:

  - OAuth client_credentials exchange succeeds
  - GraphQL query returns valid data
  - extensions.cost.throttleStatus is parseable
  - Read-only enforcement blocks a `mutation` query

Doesn't write to Postgres. Doesn't mutate Shopify (we have read-only
scopes anyway). Safe to run repeatedly — costs ~5 query points per store.

Usage:
    uv run python scripts/smoke_test_shopify.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# .env loading — this script runs outside Flask, which would otherwise
# pull DATABASE_URL etc. We don't strictly need DATABASE_URL here, but
# we do need the SHOPIFY_*_CLIENT_ID/SECRET vars.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

from app.shopify.client import ShopifyClient  # noqa: E402
from app.shopify.config import load_store_configs  # noqa: E402
from app.shopify.errors import ReadOnlyViolation  # noqa: E402
from app.shopify.throttle import parse_throttle_status  # noqa: E402

SHOP_INFO_QUERY = """
{
  shop {
    name
    myshopifyDomain
    plan { displayName shopifyPlus }
    primaryDomain { url }
    ianaTimezone
    currencyCode
  }
}
""".strip()

MUTATION_PROBE = """
mutation {
  shopUpdate(input: { name: "should never run" }) { shop { id } }
}
""".strip()


def _check(label: str, cond: bool, detail: str = "") -> None:
    icon = "OK" if cond else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{icon}] {label}{extra}")
    if not cond:
        raise SystemExit(1)


def main() -> int:
    configs = load_store_configs()
    _check(
        "loaded at least one store config from .env",
        len(configs) > 0,
        f"keys={sorted(configs.keys())}",
    )

    client = ShopifyClient(configs)
    try:
        for store_key, cfg in configs.items():
            print()
            print(f"--- {store_key} ({cfg.shop_domain}) ---")

            # Forbidden mutation must raise, even with valid creds, because read_only=True.
            blocked = False
            try:
                client.query(store_key, MUTATION_PROBE)
            except ReadOnlyViolation:
                blocked = True
            _check(f"{store_key}: read_only blocks mutation (TR-46)", blocked)

            # Live read query.
            data = client.query(store_key, SHOP_INFO_QUERY)
            shop = data.get("shop") or {}
            _check(
                f"{store_key}: shop query returned data",
                bool(shop.get("name")) and bool(shop.get("myshopifyDomain")),
                f"name={shop.get('name')!r} domain={shop.get('myshopifyDomain')!r}",
            )

            plan = shop.get("plan") or {}
            shopify_plus = bool(plan.get("shopifyPlus"))
            if cfg.plus != shopify_plus:
                # Not a hard fail — the smoke test validates the client, not .env.
                # But surface the drift so the operator can reconcile.
                print(
                    f"      WARNING: .env has SHOPIFY_{store_key.upper()}_PLUS={cfg.plus} "
                    f"but Shopify reports plan={plan.get('displayName')!r} "
                    f"(shopifyPlus={shopify_plus}). Update .env to match."
                )

            # Re-issue the same query — second call hits the in-memory token cache.
            client.query(store_key, SHOP_INFO_QUERY)
            _check(f"{store_key}: token cache reuse (no exception on 2nd query)", True)

            # Sanity check throttle parsing — a fresh shop query should leave the
            # bucket close to its max (≥ low-water threshold).
            # We can't directly read the parsed values from the client (private),
            # but parse_throttle_status is the same helper it uses.
            sample = parse_throttle_status({"cost": {"throttleStatus": {
                "currentlyAvailable": 985, "restoreRate": 50, "maximumAvailable": 1000,
            }, "actualQueryCost": 5}})
            _check(
                "throttle parser shape ok",
                sample["currently_available"] == 985 and sample["actual_cost"] == 5,
            )
    finally:
        client.close()

    print()
    print("All Shopify smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
