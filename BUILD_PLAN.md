# Build Plan

5-phase roadmap. Aligned with design doc Section 19. Phase 0 is **blocking** — no Phase 1 code lands until Phase 0 worksheets in `docs/discovery.md` are filled in. Subsequent phases overlap only where the design explicitly allows it (e.g., subscriptions in Phase 3 may proceed in parallel with analytics).

Each phase has concrete deliverables, the relevant TRs from `TECHNICAL_REQUIREMENTS.md`, and a single end-to-end acceptance check.

---

## Phase 0 — Discovery (1 week)

**Goal:** Answer the open questions from design Section 3 before sizing or building. Without these, scope and rate-limit budgeting are guesses.

| Item | Owner action | Lands in |
|---|---|---|
| Plus status per store | Settings → Plan in each store admin | `docs/discovery.md` |
| Subscription app per store + provider type | Inspect installed apps; ask store owners | `docs/discovery.md` |
| Location count per store | GraphQL `locations` connection probe | `docs/discovery.md` |
| Analytics tooling per store (GA4 / Meta / native only) | Inspect themes; ask marketing | `docs/discovery.md` |
| Volume profile (orders/day, SKU count, variant count) | One-time GraphQL count queries | `docs/discovery.md` |
| ShopifyQL `sessions` probe per store | Run probe query against each store | Pass/fail flag in `docs/discovery.md` |
| Custom App provisioning (or document existing) per store | Shopify Dev Dashboard or CLI (Admin UI no longer available as of 2026-01-01) | Tokens to `.env` (dev) / secret manager (prod) |

**Phase 0 acceptance:** every row in `docs/discovery.md` has a confirmed value (not `TBD`), and per-store access tokens exist in `.env` or the chosen secret manager.

---

## Phase 1 — Foundation, domain, repositories (2–3 weeks)

**Covers TRs:** TR-1, TR-3, TR-7, TR-8, TR-9, TR-10, TR-12, TR-13, TR-14, TR-17, TR-18, TR-19, TR-20, TR-21, TR-22, TR-23, TR-24, TR-25, TR-26.

1. Repo skeleton matching the layout in design Section 11.2.
2. `pyproject.toml` declares: Python 3.12+, Flask, SQLAlchemy 2.x, Alembic, gql, httpx, pydantic v2, FastMCP, Strawberry-GraphQL (or Ariadne), RQ (or Celery + Redis), dependency-injector, ruff, mypy, pytest, pytest-postgresql, hypothesis (optional).
3. Postgres schema + initial Alembic migration (`alembic/versions/0001_initial_schema.py`) covering every table in TR-17 with the indexes in TR-18 and money columns per TR-19.
4. Domain dataclasses (`app/domain/models.py`) and repository protocols (`app/domain/repositories.py`) — pure Python, no SQLAlchemy.
5. SQLAlchemy ORM models (`app/db/orm/`) and concrete repositories (`app/repositories/`).
6. `UnitOfWork` (`app/db/unit_of_work.py`); DI container (`app/container.py`); `InMemory*` repository fakes for tests in `tests/fakes/`.
7. Shopify GraphQL client (`app/shopify/client.py`) with cost-aware throttle and HMAC helpers.
8. Webhook receiver (`app/blueprints/webhooks/`) with HMAC-before-parse, raw-body capture, enqueue → worker dispatch.
9. Bulk Operations runner (`app/shopify/bulk.py`).
10. Initial bulk import job: `flask sync init` populates orders, products, variants, inventory, locations for all three stores.
11. Webhook subscription registration: `flask shopify register-webhooks --base-url <tunnel>` for `orders/*` and `inventory_levels/update`.
12. Pre-commit hook with `ruff check --select TID`; CI runs `pytest tests/architecture/`.

**Phase 1 acceptance:** `docker compose up`, `flask sync init`, register webhooks via tunnel, place a test order in each store, confirm it lands in Postgres within 30 seconds. Import-graph and ruff TID checks pass.

---

## Phase 2 — Service & serving surfaces (2–3 weeks)

**Covers TRs:** TR-4, TR-6, TR-32, TR-33, TR-36, TR-38, TR-42, TR-44, TR-46.

1. `OrderQueryService`, `InventoryReportingService`, `StoreComparisonService`. The `SyncOrchestrator` from Phase 1 grows to coordinate webhook + on-demand + bulk reconciliation explicitly.
2. REST API v1 endpoints (TR-32) — every route is thin; calls services; never imports SQLAlchemy.
3. GraphQL gateway (TR-33) wired through the same services.
4. Internal dashboard MVP (HTMX): cross-store order list, product list, low-stock view.
5. Bearer-token auth for the internal API plus a Flask admin view to mint tokens (TR-4).
6. Service-layer unit tests using `InMemory*` fakes (TR-42); contract tests for routes (TR-44).
7. Audit log writes for every API call (TR-6).

**Phase 2 acceptance:** `curl /api/v1/orders?store_id=...&since=...&until=...` returns paginated cross-store orders; the dashboard's cross-store order page renders; all import-graph tests still green.

---

## Phase 3 — Analytics & subscriptions (2 weeks)

**Covers TRs:** TR-11, TR-27, TR-28, TR-29, TR-30, TR-31, TR-37.

1. Nightly ShopifyQL pull → `sessions_daily` (TR-29).
2. GA4 fallback adapter wired for any store that failed the Phase 0 probe (TR-30).
3. `SubscriptionProvider` protocol + `OrderGrooveProvider` (confirmed in Phase 0 as the subscription platform on all three stores). Sub-task: confirm whether OrderGroove writes to native `SubscriptionContract` on these stores; if yes, the provider may merge data from both sources (TR-27, TR-28).
4. `analytics_kpi_daily` rollup table + dashboard view.
5. Tier-3 derived metrics in `AnalyticsService` (TR-31).
6. Add `freshness` field to all cached responses (TR-37).

**Phase 3 acceptance:** `/api/v1/analytics/daily?store_id=...&since=...&until=...` returns sessions, orders, units, revenue, and conversion for every (store, date) in the window — sourced from ShopifyQL where the probe passed and GA4 elsewhere, transparent to the caller.

---

## Phase 4 — MCP server (1–2 weeks)

**Covers TRs:** TR-5, TR-6, TR-34, TR-35, TR-36, TR-37.

1. FastMCP server in `mcp_server/` with the 12 read-only tools from TR-34.
2. Each tool is a thin function dispatching to an existing service method — no MCP-specific SQL.
3. Pydantic schemas for every tool input/output, with field descriptions (the LLM reads these).
4. Date input normalizer (`yesterday`, `7d`, `last_week` → `datetime`) shared across tools (TR-35).
5. Per-tool audit logging into `api_audit_log` (TR-6).
6. stdio entrypoint config snippet for Claude Desktop in the README. HTTP transport behind bearer token for shared use (TR-5).

**Phase 4 acceptance:** register the MCP server in Claude Desktop and ask: *"compare last week's revenue across all three stores."* Receive a response that resolves through `compare_stores` → `StoreComparisonService` → repository → cached Postgres rows. The numbers reconcile against the equivalent REST call.

---

## Phase 5 — Hardening & deploy (1–2 weeks)

**Covers TRs:** TR-39, TR-40, TR-41, TR-43, TR-45, TR-47.

1. Observability dashboards + alerts (TR-39, TR-40).
2. OpenTelemetry instrumentation hooks present, off by default (TR-41).
3. Repository integration tests against Postgres in Docker (TR-43); end-to-end webhook-to-DB test using replayed bodies (TR-45).
4. PII masking in dashboard + logs (TR-47).
5. Containerization (Dockerfile multi-entrypoint) + deploy to Render (or chosen target).
6. Documentation: ops runbook, scope/credential rotation procedure, MCP tool reference for end users.
7. Tabletop exercises: webhook outage, token rotation, store credential revocation.

**Phase 5 acceptance:** the system runs on the chosen production target; webhooks flow through public TLS; alerts fire on injected failure scenarios; the runbook walks through token rotation end-to-end.

---

## Cross-phase CI gates

These run on every commit from Phase 1 onward:

| Gate | What it checks |
|---|---|
| `ruff check --select TID` | Layer import rules (TR-25). |
| `pytest tests/architecture/` | Import-graph layer enforcement (TR-26). |
| `pytest tests/unit/` | Service-layer behavior with in-memory fakes (TR-42). |
| `pytest tests/integration/` | Repositories against Postgres (TR-43). |
| `pytest tests/contract/` | HTTP and MCP route shapes (TR-44). |
| `pip-audit` (or equivalent) | Dependency vulnerability scan. |
| `mypy app/ mcp_server/` | Static type check. |

---

## Risk register (from design Section 18)

| Risk | Mitigation |
|---|---|
| Plus vs non-Plus status differs per store | Phase 0 confirms before sizing. |
| Subscription app differs per store | Provider adapter pattern absorbs the difference (TR-27). |
| ShopifyQL surface continues to evolve | Probe-and-verify per metric; GA4 fallback (TR-11, TR-30). |
| Webhook delivery is best-effort | Nightly Bulk reconciliation closes the gap (TR-15). |
| Custom App creation path changed Jan 2026 | Use Shopify Dev Dashboard or CLI for any new apps. |
| Token compromise across three stores | Encrypted at rest, redacted from logs, scope-limited, rotatable (TR-1, TR-47). |
| Claude calls trigger expensive Shopify calls | MCP tools default to cache-only; per-MCP-caller rate limits (TR-35). |
| Layer-rule violations creep in over time | Pre-commit lint + CI import-graph tests (TR-25, TR-26). |
