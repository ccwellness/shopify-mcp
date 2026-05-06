# Technical Requirements

Numbered, testable requirements traceable from build tasks → tests → acceptance. Source: `docs/design_requirements.docx`, v0.1 (2026-04-29).

Requirements are grouped by concern. Each TR is intended to be checkable as either passing or failing — no aspirations, no "nice-to-haves." When a requirement requires a test, the test belongs in `tests/` under the matching layer (unit / integration / contract / architecture / e2e).

---

## Authentication & Secrets

| ID | Requirement |
|----|-------------|
| **TR-1** | Per-store OAuth 2 **client credentials** (`client_id` + `client_secret` from the Shopify Dev Dashboard) loaded from env in dev, from a `secrets` abstraction in prod. The Shopify client exchanges these for short-lived (24h) access tokens via the `client_credentials` grant against `https://{shop}.myshopify.com/admin/oauth/access_token`, caches tokens in memory, and refreshes on demand. Neither the credentials nor the resulting access tokens are ever logged; logging middleware redacts any header named `X-Shopify-Access-Token`. |
| **TR-2** | Each store's Custom App requests these read scopes: **required (10)** — `read_orders`, `read_all_orders`, `read_products`, `read_inventory`, `read_locations`, `read_customers`, `read_fulfillments`, `read_own_subscription_contracts`, `read_analytics`, `read_reports`; **recommended (1)** — `read_returns` (return/refund reporting). `read_reports` is required by `shopifyqlQuery` (confirmed via Phase 0 probe — without it the field returns `ACCESS_DENIED`). `read_shipping` was dropped from the required list in Phase 0: it gates the *shipping configuration* surface (zones, rates, carrier services) which v1 does not use; per-order shipping costs and addresses come through `read_orders`. Level-2 protected-customer-data access is **not** required for the analytics queries we run (sessions, total_sales, orders) — confirmed empirically. |
| **TR-3** | Webhook HMAC verification uses `HMAC-SHA256(body, app_shared_secret)` with `hmac.compare_digest`. Verification runs **before any other handler logic**. Failure → HTTP 401, no enqueue, no DB write. |
| **TR-4** | Internal API uses bearer tokens issued via a Flask admin view. Tokens default to read-only, support optional per-store scoping, and expire on a configurable cadence (default 90 days). |
| **TR-5** | MCP HTTP transport requires a bearer token in the `Authorization` header (same token store as the REST API). MCP stdio transport relies on local OS user authorization. |
| **TR-6** | Every API and MCP tool invocation writes a row to `api_audit_log` with caller identity, store, route/tool, and **sanitized** parameters (no PII, no raw query bodies). |

## Shopify Integration

| ID | Requirement |
|----|-------------|
| **TR-7** | All new Shopify calls use the **GraphQL Admin API**. The application pins a specific quarterly version (`2026-04` at design time) and upgrades on a quarterly cadence. |
| **TR-8** | The GraphQL client inspects `extensions.cost.throttleStatus` on every response and applies exponential backoff with jitter when `currentlyAvailable < 100` points. `THROTTLED` errors are retried with the same backoff. |
| **TR-9** | Per-store GraphQL cost consumption is emitted as a Prometheus / StatsD counter so saturation is visible early. |
| **TR-10** | The Bulk Operations runner enforces Shopify's "one bulk op per shop at a time" constraint by serializing requests per shop. The runner downloads JSONL output before the temporary URL expires. |
| **TR-11** | Each store has a passing **ShopifyQL probe query for `sessions`** before any code depends on the metric. A failed probe routes that store's analytics through the GA4 fallback for that metric. |

## Sync

| ID | Requirement |
|----|-------------|
| **TR-12** | Webhook handlers respond `200 OK` within Shopify's 5-second budget. Real work happens in an RQ (or Celery) worker, not in the request handler. |
| **TR-13** | The application subscribes to these topics (design Section 8.1): `orders/create`, `orders/updated`, `orders/paid`, `orders/cancelled`, `orders/fulfilled`, `fulfillments/create`, `fulfillments/update`, `products/create`, `products/update`, `products/delete`, `inventory_levels/update`, `customers/create`, `customers/update`, `app/uninstalled`. |
| **TR-14** | Every webhook delivery writes a row to `webhook_events_log` with the **raw (compressed) payload** *before* any processing. |
| **TR-15** | A nightly Bulk Operation per store reconciles orders modified in the last 48 hours **plus** a full inventory snapshot. Reconciliation repairs cache rows missed by webhook outages. |
| **TR-16** | An on-demand GraphQL fetcher is available for cache-miss reads or freshness-critical requests. Fetched results write through to the cache. |

## Data Model

| ID | Requirement |
|----|-------------|
| **TR-17** | Every business table carries a non-nullable `store_id` column. Required tables: `stores`, `locations`, `products`, `variants`, `inventory_items`, `inventory_levels`, `customers`, `orders`, `order_line_items`, `order_shipping_addresses`, `fulfillments`, `subscription_contracts`, `sessions_daily`, `analytics_kpi_daily`, `sync_state`, `webhook_events_log`, `api_audit_log`. |
| **TR-18** | Compound indexes exist on at least: `(store_id, processed_at)`, `(store_id, sku)`, `(store_id, location_id)`. |
| **TR-19** | Money columns are `numeric(19,4)` in the shop's currency, with separate columns where presentment currency differs. |
| **TR-20** | Shopify identifiers are stored as the full GID (`gid://shopify/Order/12345`) **plus** a parsed numeric column for sort/range performance. |

## Architecture / Code Discipline

| ID | Requirement |
|----|-------------|
| **TR-21** | Repository **interfaces** are Python `Protocol` classes in `app/domain/repositories.py`. Concrete implementations live in `app/repositories/`. The domain layer never imports SQLAlchemy. |
| **TR-22** | Repositories return frozen dataclasses or pydantic models. **ORM rows must never escape `app/repositories/`.** |
| **TR-23** | Transactions are managed by a `UnitOfWork` context manager that exposes the repositories. Services call repositories only inside a UoW block. Commit and rollback are explicit. |
| **TR-24** | `app/container.py` builds the dependency-injection container. Both the Flask `create_app()` factory and the MCP server entrypoint pull pre-built service instances from this container. |
| **TR-25** | Pre-commit `ruff check --select TID` import rules forbid: `sqlalchemy` outside `app.repositories.*` and `app.db.*`; `app.repositories.*` outside `app.services.*` and `app.container`; `app.db.*` outside `app.repositories.*`. |
| **TR-26** | `tests/architecture/` contains import-graph tests that fail CI on any layer-rule violation. |

## Subscriptions

| ID | Requirement |
|----|-------------|
| **TR-27** | A `SubscriptionProvider` `Protocol` has at least two implementations: `NativeProvider` (reads `SubscriptionContract` via Admin GraphQL) and an `OrderGrooveProvider` — confirmed in Phase 0 discovery as the subscription platform on all three stores. The `OrderGrooveProvider` uses OrderGroove's REST API; whether it also reads from native `SubscriptionContract` (hybrid integration) is a sub-question to confirm before Phase 3 starts. |
| **TR-28** | Both provider implementations write to the same `subscription_contracts` table. Downstream consumers (dashboard, REST, MCP) are app-agnostic. |

## Analytics

| ID | Requirement |
|----|-------------|
| **TR-29** | A nightly job pulls Tier-1 metrics (sessions, orders, total_sales) via ShopifyQL into `sessions_daily` per (store, date). Working query shape (verified Phase 0 on lubelife): `FROM sales, sessions SHOW day, total_sales, orders, sessions GROUP BY day SINCE -<N>d UNTIL -1d`. The `read_reports` scope is required (TR-2). ShopifyQL has its own per-window cost budget (`shopifyqlCost`, max 1000) separate from the standard GraphQL throttle bucket — observe both. |
| **TR-30** | A Tier-2 GA4 fallback adapter exists per store. It runs for any metric × store combination where the ShopifyQL probe failed (TR-11) or the metric is Plus-gated and the store is not on Plus. |
| **TR-31** | Tier-3 derived metrics (conversion rate, AOV, etc.) are computed in application code with formulas that are byte-identical across all three stores. |

## Serving Surfaces

| ID | Requirement |
|----|-------------|
| **TR-32** | REST endpoints exist under `/api/v1/`: `stores`, `orders` (list + detail), `products` (list + detail), `inventory`, `subscriptions`, `analytics/daily`, and `sync/orders/{id}/refresh`. Routes are thin: parse → service call → serialize. |
| **TR-32a** | The data layer persists every Shopify location faithfully (Shopify is source of truth; order/fulfillment history depends on it). Inventory and location-facing endpoints / MCP tools surface locations through a service-layer filter that uses **inventory presence and recent fulfillment activity** — not just the `isActive` config flag, and not just `fulfillsOnlineOrders`/`shipsInventory` either. Reason: Phase 0 found stale duplicates flagged `isActive=true` and `fulfillsOnlineOrders=true` on both lubelife and shopjo, even though they hold no inventory and ship nothing. Concretely: a location is "operationally real" if `(inventory_levels_total > 0) OR (fulfillments_in_last_90d > 0)`. The exact thresholds and join shape are a Phase 1 implementation detail; the principle is that operational signals come from data, not config flags. |
| **TR-33** | A GraphQL gateway at `/graphql` (Strawberry) exposes the schema sketched in design Section 12.2. Resolvers call services, never the ORM. |
| **TR-34** | The MCP server exposes these read-only tools (design Section 13.3): `list_stores`, `list_orders`, `get_order`, `search_orders_by_customer`, `list_products`, `get_product`, `check_inventory`, `list_low_stock`, `get_kpis`, `compare_stores`, `list_subscriptions`, `refresh_order`. |
| **TR-35** | MCP tools accept ISO 8601 datetimes **and** relative phrases (`yesterday`, `7d`, `last_week`). Tools default to **cache-only**; live fetches require explicit `refresh_*` tools. |
| **TR-36** | Every MCP and REST list response is paginated to ≤ 50 rows with a `next_cursor` field for follow-up calls. |
| **TR-37** | Every cached row response includes a `freshness` field (cache age in seconds) so the LLM can decide whether to call `refresh_order`. |

## Observability & Ops

| ID | Requirement |
|----|-------------|
| **TR-38** | Logs are structured JSON to stdout with `request_id`, `store_id`, `route`, `latency_ms`, `status`, and (for Shopify calls) `query_cost`. |
| **TR-39** | Per-store dashboards exist for: webhook delivery success rate, queue depth, GraphQL cost consumption, and sync lag (`max(now() - last_seen)` per resource). |
| **TR-40** | Alerts fire on: webhook auth failures > 0 in 5 min, queue depth > N for 10 min, sync lag > 30 min on orders, GraphQL throttle errors > 5% of calls. |
| **TR-41** | OpenTelemetry hooks are present in the codebase so distributed tracing can be enabled without code changes. |

## Testing

| ID | Requirement |
|----|-------------|
| **TR-42** | Service-layer unit tests use `InMemory*` repository fakes from `tests/fakes/` and **never** touch Postgres. |
| **TR-43** | Repository integration tests run against real Postgres in Docker, with rolled-back transactions per test for isolation. |
| **TR-44** | Contract tests for Flask routes and MCP tools use stubbed services that return canned domain objects. |
| **TR-45** | At least one end-to-end test exercises the webhook-to-DB pipeline against a Shopify dev store using replayed webhook bodies. |

## Safety

| ID | Requirement |
|----|-------------|
| **TR-46** | v1 is **read-only** against production stores. The Shopify client enforces a per-store `read_only` flag that blocks any mutation call at runtime. |
| **TR-47** | No customer PII appears in logs. The dashboard masks email and phone by default and reveals on click with an audit-trail entry. |
