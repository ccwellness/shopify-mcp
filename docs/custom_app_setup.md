# Custom App provisioning — step-by-step

You'll do this **three times**, once per store. Each store gets its own Custom App and its own pair of credentials. The flow takes ~5-10 minutes per store once you've done the first one.

> **Important — the auth model changed on 2026-01-01.** Legacy Shopify Custom Apps in the store admin issued a permanent `shpat_...` access token. Those can no longer be created. New Custom Apps live in the **Shopify Dev Dashboard** and use **OAuth 2 client credentials**: you receive a `client_id` and `client_secret` per app, and your server exchanges those for a 24-hour access token whenever you need to call the API. Refresh is just re-calling the token endpoint.

---

## 0. Before you start

- **Sign-in:** make sure you can sign in to <https://dev.shopify.com/dashboard/> using the Shopify account tied to your stores. If the stores are part of a Shopify Plus organization, the Dashboard may show you the organization view; that's fine.
- **Permissions:** you need store-owner-level access (or app-developer permission) on each of the three stores. As the operator, you almost certainly have this.
- **A safe place to paste credentials:** the `.env` file in this folder is the dev target. **Do not paste secrets into chat / email / Slack / shared docs** — the `client_secret` is as sensitive as a database password. (Also note: secrets in OneDrive get synced to Microsoft's cloud even if they're in `.gitignore`. The project lives at `C:\Users\jodom\projects\shopify_connector\` precisely to avoid that.)

---

## 1. Sign in to the Dev Dashboard

Browse to <https://dev.shopify.com/dashboard/> and sign in with the Shopify account tied to your stores. You'll land on the Dashboard home, which lists any apps you've already created.

You can also reach it from inside any store admin via **Settings → Apps and sales channels → Develop apps → Build apps** — that link redirects to the Dev Dashboard.

---

## 2. Create the app

Make sure **Apps** is selected in the left-nav. Click **Create app** in the top right of the screen, then pick **Start from Dev Dashboard**.

Name the app something descriptive that includes the store — e.g., `CCW Multi-Store Connector – lubelife`. The name is internal-only. You'll create three apps total (one per store), so name them with the store to keep the dashboard list readable. Click **Create**.

---

## 3. Create a version

Once the app is created, go to the **Versions** tab. A version is the bundle of configuration (URL, webhook API version, scopes) that gets installed on the store. You release a version once before you can install the app.

Fill in:

- **App URL:** the default `https://shopify.dev/apps/default-app-home` is fine for an internal connector — we don't host a frontend the merchant interacts with.
- **Webhooks API version:** pick `2026-04` to match the connector's pinned API version (`SHOPIFY_API_VERSION` in `.env`).
- **Required scopes (10):** enable these read-only scopes — the connector is read-only in v1 (TR-46):

| Scope | Why we need it |
|---|---|
| `read_orders` | Order objects within last 60 days. |
| `read_all_orders` | Order objects older than 60 days — required for historical reporting. |
| `read_products` | Products and variants. |
| `read_inventory` | InventoryItem and InventoryLevel data. |
| `read_locations` | Location list (multi-location inventory). |
| `read_customers` | Customer records linked to orders. |
| `read_fulfillments` | Fulfillment records and tracking. |
| `read_own_subscription_contracts` | Native Shopify subscription contracts (hedge in case OrderGroove writes to native). |
| `read_analytics` | Legacy analytics surfaces. |
| `read_reports` | **Required for `shopifyqlQuery`** — without this, ShopifyQL returns `ACCESS_DENIED`. |

- **Recommended (1):**

| Scope | Why |
|---|---|
| `read_returns` | Return / refund reporting. Useful, low-risk, no review required. |

- **Intentionally not requested:** `read_shipping` was on early drafts but removed after Phase 0 — it gates the shipping-configuration surface (zones, carrier services, rates) which v1 does not use. Per-order shipping costs and addresses come through `read_orders`. Add it back if v2 grows shipping-rate features.

> **Phase 0 follow-up:** some ShopifyQL metrics that join to customer PII (name/email/phone/address) additionally require **Level-2 protected-customer-data access** via a separate Dev Dashboard attestation flow. We won't know whether the metrics we actually need (sessions, total_sales, orders) trigger that requirement until we re-probe with `read_reports` enabled. If the probe still fails after adding `read_reports`, the next step is to complete the protected-customer-data attestation in the Dashboard's app Configuration screen.

Click **Release** to ship the version.

> Do **not** enable any `write_*` scope. v1 is strictly read-only and the codebase enforces this at runtime via the per-store `read_only` flag (TR-46).

> **Important — the Release step is a prerequisite for step 4.** The "Install app" button on the Home tab does **not appear** until the app has at least one *released* version. If you created a version but it's still in Draft state, click into it and click **Release** before continuing. This is the most common stumble in this flow.

---

## 4. Install the app on the store

Navigate to the app's **Home** tab in the left-nav. Scroll down — the **Install app** button is below the welcome / overview content on that page. Click it, pick the target store (for the first one, choose `lubelife.myshopify.com`), confirm the scope list, then complete the install.

**If you don't see "Install app" on Home:**
- Re-check the Versions tab. Is there a version with status `Released`? If status is `Draft`, click into it and click **Release**, then return to Home.
- If no version exists at all, go back to step 3 and create one.
- If a version is `Released` and Install app still isn't visible, the page may be showing a "Get started" checklist instead — look for an Install action on that checklist, or refresh the page.

(You'll repeat this whole flow — including a separate Create app step — for `shopjo` and `shopshibari` afterward. Each store gets its own app, its own client_id, and its own client_secret.)

---

## 5. Capture the credentials

Go to the app's **Settings** tab. You're looking for two values:

- **Client ID** — public-ish identifier for the app.
- **Client secret** — the sensitive credential. Treat it like a password.

Copy both. Unlike the old `shpat_` flow, these credentials don't disappear after one view — you can come back to Settings any time. But still: paste them into `.env` and don't share them.

---

## 6. Paste into `.env`

Open `shopify_connector/.env` (create it from `.env.example` if it doesn't exist yet). For the lubelife store, fill in:

```bash
SHOPIFY_LUBELIFE_SHOP=lubelife.myshopify.com           # already set in .env.example
SHOPIFY_LUBELIFE_CLIENT_ID=<the-client-id-from-step-5>
SHOPIFY_LUBELIFE_CLIENT_SECRET=<the-client-secret-from-step-5>
```

The connector will exchange these for a 24-hour access token at runtime via the OAuth flow described in step 8.

---

## 7. Repeat for shopjo and shopshibari

Same flow, three times total. Each store gets its own app in the Dev Dashboard with the same 10 scopes. Client ID and secret values land in `SHOPIFY_SHOPJO_*` and `SHOPIFY_SHOPSHIBARI_*` respectively.

When you're done, your `.env` should have all three `SHOPIFY_*_CLIENT_ID` and `SHOPIFY_*_CLIENT_SECRET` lines populated with real values.

---

## 8. Understand the OAuth token-exchange flow

You don't have to do anything here manually — both the probe script and the Phase 1+ Shopify client handle this automatically — but it's worth knowing how it works because it's different from the legacy model:

```bash
# Per-store, when an access token is needed:
curl -X POST \
  "https://{shop}.myshopify.com/admin/oauth/access_token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id={client_id}" \
  -d "client_secret={client_secret}"
```

Response:

```json
{
  "access_token": "f85632530bf277ec9ac6f649fc327f17",
  "scope": "read_orders,read_products,...",
  "expires_in": 86399
}
```

The `access_token` value goes into the `X-Shopify-Access-Token` header on every GraphQL call. After ~24 hours the token expires; re-call the same endpoint to get a fresh one. The connector caches tokens in memory and refreshes on demand.

**Webhook HMAC signing:** legacy custom apps used the API secret as the HMAC key for verifying incoming webhooks (TR-3). The Dev Dashboard model is likely the same — `client_secret` doubles as the webhook signing key — but Shopify's docs didn't explicitly confirm this on the page checked here. Treat as a Phase 1 verification item: when the webhook receiver is built, confirm with a known-signed test delivery before relying on it.

---

## 9. Verify with the probe script

The moment at least one store's credentials are in `.env`, sanity-check by running the discovery probe script:

```powershell
# from the shopify_connector folder
python scripts\run_discovery_probes.py
```

The script does the OAuth exchange itself, then runs all probes. The first thing to check is the **Plan** row: if everything works, you'll see your plan name (e.g., "Shopify Plus") and `shopifyPlus: True` for each configured store. That confirms (a) the client_id/client_secret pair is valid, (b) the OAuth exchange succeeded, (c) the scope list matches what we asked for, and (d) we're talking to the right store.

Common failure modes:
- **HTTP 401 from `/admin/oauth/access_token`** — wrong client_id or client_secret, or app not installed on that store.
- **GraphQL error mentioning a specific scope** — that scope wasn't included in the version that's installed. Go back to step 3, fix the scope list, release a new version, reinstall.
- **Mismatched store** — credentials from app A used against store B. Each app's credentials only work on the store(s) where it's installed.

You can run the script multiple times safely. It's read-only and uses negligible API budget (~10-50 cost points per store, against the 1,000-pt bucket on Advanced / 10,000-pt on Plus).

---

## 10. When this is done

Update `docs/discovery.md` so the Custom App row and credential rows for each store say **Yes** with today's date. Then we can run the full probe script and fill in the remaining locations / volume / ShopifyQL rows in one pass.

That closes Phase 0. Phase 1 (foundation, domain, repositories) starts immediately after.

---

## CLI alternative (skip if the Dashboard worked)

For engineers comfortable with terminals, the Shopify CLI offers an equivalent path:

```bash
npm install -g @shopify/cli
shopify app init        # scaffolds an app project
shopify app deploy      # configures scopes
```

The CLI is more involved than the Dashboard UI for a simple read-only token use case, so the Dashboard is recommended unless you're already on the CLI for other reasons.

---

## UI-label drift disclaimer

Shopify ships UI updates every quarter. The button labels above are what was current at the time of this writing (April 2026). If a label has shifted, look for the conceptual equivalent: "Versions" might become "Configuration", "Install app" might become "Install on store", etc. The flow itself — Create app → Release a version with scopes → Install on a store → Copy client_id and client_secret — is stable.

If the Dashboard surfaces something I haven't described (App Bridge, theme app extensions, deferred webhook config), **skip it**. Phase 1 of the connector code subscribes to webhooks programmatically. The Dev Dashboard app just needs to exist with a released version that has the right scopes, and be installed on the store. Everything else is downstream.
