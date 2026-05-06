"""Smoke test for webhook_admin against live lubelife.

Validates two paths without actually mutating Shopify state:

  1. `list_existing(...)` — returns the shop's current subscriptions.
  2. `reconcile(..., dry_run=True)` — computes the diff against
     SUBSCRIBED_TOPICS for a stub callback URL and reports what would
     be created / relocated / pruned. NO mutations are sent.

This is safe to run against any live store — dry_run=True means no
create or delete mutation hits Shopify. Use the real CLI
(`flask shopify register-webhooks`) once a real WEBHOOK_BASE_URL
(Cloudflare tunnel or prod domain) is in place.

Usage:
    .venv\\Scripts\\python.exe scripts\\smoke_test_register_webhooks.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

from app import create_app  # noqa: E402
from app.shopify import webhook_admin  # noqa: E402
from app.shopify.config import load_store_configs  # noqa: E402
from app.shopify.webhooks import SUBSCRIBED_TOPICS  # noqa: E402

STORE_KEY = "lubelife"
STUB_BASE_URL = "https://smoke-test.example.com"  # never actually used (dry-run)


def _check(label: str, cond: bool) -> None:
    icon = "OK" if cond else "FAIL"
    print(f"[{icon}] {label}")
    if not cond:
        raise SystemExit(1)


def main() -> int:
    configs = load_store_configs()
    if STORE_KEY not in configs:
        print(f"FAIL: {STORE_KEY!r} not in loaded store configs")
        return 1

    app = create_app()
    client = app.extensions.get("shopify_client")
    if client is None:
        print("FAIL: shopify_client not wired")
        return 1

    print(f"=== list_existing({STORE_KEY}) ===")
    existing = webhook_admin.list_existing(client, STORE_KEY)
    print(f"  found {len(existing)} existing subscription(s)")
    for sub in existing[:10]:
        # show first few — ids and URLs are useful when troubleshooting
        url_preview = sub.callback_url[:60] + ("…" if len(sub.callback_url) > 60 else "")
        print(f"    {sub.topic:30s} -> {url_preview!r}  ({sub.id})")
    if len(existing) > 10:
        print(f"    ... ({len(existing) - 10} more)")

    _check("list_existing returns a list", isinstance(existing, list))
    for sub in existing:
        _check(
            f"sub {sub.id!r} has uppercase topic (got {sub.topic!r})",
            sub.topic == sub.topic.upper(),
        )

    print(f"\n=== reconcile({STORE_KEY}, dry_run=True, base_url={STUB_BASE_URL!r}) ===")
    result = webhook_admin.reconcile(
        client,
        STORE_KEY,
        base_url=STUB_BASE_URL,
        prune_unknown=False,
        dry_run=True,
    )
    print(f"  callback URL would be:  {result.callback_url}")
    print(f"  would create:           {len(result.created)} -> {list(result.created)}")
    print(f"  already wired correctly:{len(result.already_present)}")
    print(f"  would relocate:         {len(result.relocated)} -> {list(result.relocated)}")

    _check(
        f"callback URL ends with /webhooks/{STORE_KEY} (got {result.callback_url!r})",
        result.callback_url.endswith(f"/webhooks/{STORE_KEY}"),
    )
    total_planned = len(result.created) + len(result.already_present) + len(result.relocated)
    _check(
        f"plan covers all {len(SUBSCRIBED_TOPICS)} allow-list topics (got {total_planned})",
        total_planned == len(SUBSCRIBED_TOPICS),
    )
    overlap = set(result.created) & set(result.already_present)
    _check(f"created and already_present sets disjoint (overlap={overlap})", not overlap)

    print()
    print("All webhook-admin smoke checks passed (no mutations sent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
