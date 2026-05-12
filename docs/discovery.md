# Phase 0 Discovery Worksheet

Fill this in **before** Phase 1 code lands. The values here drive scope, rate-limit budgeting, and which subscription / analytics adapters get implemented first.

Source of authority: design doc Section 3 ("Stores and Discovery Items") and Section 18 ("Risks and Open Questions").

---

## Per-store status

Replace `TBD` with confirmed values. Date the entry when you fill it in.

### lubelife.com

| Item | Value | Source / Method | Confirmed on |
|---|---|---|---|
| Shop domain | `lubelife.myshopify.com` | known | — |
| Shopify Plus? | **No --- plan is `Advanced`** (probe overrides earlier user statement) | Probe `shop.plan.shopifyPlus = False` | 2026-04-30 |
| Subscription provider | **OrderGroove** (third-party) | User confirmation (2026-04-29) | 2026-04-29 |
| OrderGroove integration depth | **OrderGroove-only — does NOT write to native `SubscriptionContract`.** Probe `{ subscriptionContracts(first: 5) }` returned 0 edges with `hasNextPage: false` (no `ACCESS_DENIED` so `read_own_subscription_contracts` scope is present). OrderGrooveProvider must use OrderGroove's REST API; OrderGroove API credentials needed in `.env` before building the adapter. | Probe via `ShopifyClient` against the Admin GraphQL API | 2026-05-12 |
| Location count | **3 in Shopify, but only 1 operationally** --- all share address `24903 Avenue Kearny, Santa Clarita, CA 91355`. Only `Kearny` has `fulfillsOnlineOrders=true` and `shipsInventory=true`; `CC Wellness LLC` and `Kearny - Do Not Use` are stale duplicates that were never deactivated. The connector ingests all 3 (Shopify is source of truth) but service-layer reports filter on `fulfillsOnlineOrders OR shipsInventory`. | Probe `locations` connection with address + flags | 2026-04-30 |
| Analytics tooling | **Shopify native + GA4 + Meta Pixel** | User confirmation (2026-04-29) | 2026-04-29 |
| Volume — orders/day | **~26/day** (788 in last 30d) | Probe `ordersCount` | 2026-04-30 |
| Volume — products | **62** | Probe `productsCount` | 2026-04-30 |
| Volume — variants | **78** | Probe `productVariantsCount` | 2026-04-30 |
| ShopifyQL `sessions` probe | **PASS** --- 6 days returned with `day`, `total_sales`, `orders`, `sessions` columns populated. Working query: `FROM sales, sessions SHOW day, total_sales, orders, sessions GROUP BY day SINCE -7d UNTIL -1d`. ~1k sessions/day, conversion rate ~2.6% (sanity-checks vs 26 orders/day). | Probe (TR-11) post-`read_reports` scope add | 2026-04-30 |
| Level-2 protected customer data access | **Not required** --- the analytics queries we care about (sessions, total_sales, orders) succeed without it. May still be required for any future query that joins to customer PII. | Confirmed empirically | 2026-04-30 |
| GA4 fallback required? | **No --- defensive only** for v1. ShopifyQL fully covers our analytics needs even on Advanced. GA4 adapter stays in scope as a safety net per TR-30 but isn't the primary path. | Resolved by ShopifyQL PASS | 2026-04-30 |
| Custom App provisioned in Dev Dashboard? | **Yes** | Installed via Dev Dashboard | 2026-04-30 |
| Client ID + Client Secret captured? | **Yes** (in `.env`) | OAuth exchange succeeded against `/admin/oauth/access_token` | 2026-04-30 |
| Webhook signing key confirmed? | TBD (likely = client_secret; verify with first signed delivery) | Phase 1 webhook receiver verification | TBD |

### shopjo.com

| Item | Value | Source / Method | Confirmed on |
|---|---|---|---|
| Customer-facing domain | `shopjo.com` | known | — |
| Shopify handle / `myshopify.com` | **`system-jo.myshopify.com`** | OAuth 404 on `shopjo.myshopify.com` led to user-supplied actual handle | 2026-04-30 |
| Shopify Plus? | **No --- plan is `Advanced`** (same as lubelife; pattern: all CCW stores were originally reported as Plus but probes show Advanced) | Probe `shop.plan.shopifyPlus = False` | 2026-04-30 |
| Subscription provider | **OrderGroove** (third-party) | User confirmation (2026-04-29) | 2026-04-29 |
| OrderGroove integration depth | **OrderGroove-only — same probe result as lubelife** (0 edges, no `ACCESS_DENIED`). Both stores need OrderGroove REST API credentials before the adapter can be built. | Probe via `ShopifyClient` against the Admin GraphQL API | 2026-05-12 |
| Location count | **3 in Shopify, ~2 effective** --- `Avenue Kearny, CA` (online+ships, Kearny CA address) is primary; `Consumer Events - Virtual Location` (Las Vegas NV, 3000 S Las Vegas Blvd) is a legitimate event/popup location; `Warehouse` (online only, same Kearny address) is a probable stale dupe. | Probe `locations` connection with address + flags | 2026-04-30 |
| Analytics tooling | **Shopify native + GA4 + Meta Pixel** | User confirmation (2026-04-29) | 2026-04-29 |
| Volume — orders/day | **~10/day** (299 in last 30d) | Probe `ordersCount` | 2026-04-30 |
| Volume — products | **88** | Probe `productsCount` | 2026-04-30 |
| Volume — variants | **142** | Probe `productVariantsCount` | 2026-04-30 |
| ShopifyQL `sessions` probe | **PASS** --- 6 days returned with `day`, `total_sales`, `orders`, `sessions` columns populated. Same working query as lubelife. | Probe (TR-11) | 2026-04-30 |
| Custom App provisioned in Dev Dashboard? | **Yes** | OAuth exchange succeeded after handle correction | 2026-04-30 |
| Client ID + Client Secret captured? | **Yes** (in `.env`) | OAuth verified | 2026-04-30 |
| Webhook signing key confirmed? | TBD (likely = client_secret) | Phase 1 webhook receiver verification | TBD |
| Client ID + Client Secret captured? | TBD | Stored in `.env` or secret manager | TBD |
| Webhook signing key confirmed? | TBD (likely = client_secret) | Phase 1 webhook receiver verification | TBD |

### shopshibari.com  ---  **DEFERRED**

Phase 0 for shopshibari is paused as of 2026-04-30. Blocker: the Shopify Dev Dashboard does not appear to be enabled / accessible for this store from the user's current account. Settings → Apps and sales channels shows only the informational "Build and manage apps in your Dev Dashboard" panel with a "Learn More" button --- no enable action proceeds, and shopshibari does not appear in the store picker when creating an app from <https://dev.shopify.com/dashboard/>.

**Likely root causes** (not yet diagnosed):
- shopshibari may be owned by a different Shopify account than lubelife / shopjo (the user's current dev-dashboard session is tied to a specific account).
- Or shopshibari's owner has not yet performed the one-time "enable Dev Dashboard for this store" handshake.
- Or the user's role on shopshibari is staff-without-develop-apps-permission, even though admin sign-in works.

**To unblock when we come back:**
1. Identify the store-owner email for shopshibari (Settings → Plan in shopshibari admin).
2. Either sign into dev.shopify.com as that owner and provision from there, or have the owner grant "Develop apps" permission to the current dev-dashboard account.
3. Then follow `docs/custom_app_setup.md` like the other two stores --- 11 scopes, paste creds into `.env`, run probe.

The connector's data model and code path is already multi-store; adding shopshibari later is config-only (env vars + one row in the `stores` table), no code change required.

| Item | Value | Source / Method | Confirmed on |
|---|---|---|---|
| Customer-facing domain | `shopshibari.com` | known | — |
| Shopify handle / `myshopify.com` | **TBD** (may not be `shopshibari.myshopify.com`; confirm when access is restored) | Pending | TBD |
| Shopify Plus? | **TBD** (very likely `Advanced` based on lubelife + shopjo pattern) | Probe required | TBD |
| Subscription provider | **OrderGroove** (assumed --- consistent with other two stores) | User confirmation (2026-04-29) | 2026-04-29 |
| Custom App provisioned in Dev Dashboard? | **No --- blocked on Dev Dashboard access** | See above | 2026-04-30 (paused) |
| All other items | TBD | Pending probe | TBD |

---

## Running the probes

Once at least one access token has been captured into `.env`, run:

```bash
python scripts/run_discovery_probes.py
```

The script automates everything below — `locations`, `ordersCount` (30d), `productsCount`, `productVariantsCount`, and the ShopifyQL `sessions` probe — for all three stores in one pass. Stores whose tokens are still placeholders are skipped with a clear note. Output is markdown; pipe to `docs/probe_results.md` if you want to commit it.

The reference queries below are kept for anyone running the probes by hand (e.g., from the Shopify Admin GraphiQL app).

## Probe queries (reference)

### ShopifyQL `sessions` probe (TR-11)

Run via the GraphQL Admin API at `POST /admin/api/2026-04/graphql.json` with the per-store access token in `X-Shopify-Access-Token`.

```graphql
{
  shopifyqlQuery(query: "FROM sales SHOW sessions, total_sales, orders SINCE -7d UNTIL -1d") {
    ... on TableResponse {
      tableData {
        rowData
        columns { name dataType }
      }
    }
    ... on ParseError { code message }
  }
}
```

**Pass criterion:** `rowData` is non-empty for the last 7 days. **Fail:** ParseError, empty rows, or `sessions` column missing → that store routes through GA4 (TR-30).

### Locations probe

```graphql
{
  locations(first: 50) {
    edges {
      node { id name address { city country } isActive }
    }
  }
}
```

### Volume probes

```graphql
# Orders in the last 30 days
{ ordersCount(query: "created_at:>=2026-03-30") { count } }

# Total products and variants
{ productsCount { count } }
```

---

## Decisions to record once everything is filled in

- [ ] Per-store **rate-limit budget**. Confirmed empirically for lubelife (Advanced plan) on 2026-04-30: bucket = **4,000 pts**, restore = **200 pts/sec** (these differ from the Shopify documentation's "Standard/Advanced = 1,000-pt / 50-pt" line; treat probe values as authoritative). Plus is still 10,000-pt / 500-pt. shopjo + shopshibari pending probe.
- [ ] Per-store **subscription adapter to build first** (native vs which third-party).
- [ ] Per-store **analytics path** (ShopifyQL primary vs GA4 fallback).
- [ ] **Initial bulk import sizing** (rough wall-clock estimate from volume probes).

When this worksheet is fully filled in and the access tokens / webhook secrets are captured, update the status checkbox in `README.md` and proceed to Phase 1.
