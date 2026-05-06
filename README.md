# Shopify Multi-Store Connector

Internal Python/Flask application that consolidates **orders, catalog, inventory, customers, fulfillments, subscriptions, and analytics** from CC Wellness's three Shopify stores — `lubelife.com`, `shopjo.com`, `shopshibari.com` — into one Postgres database, then exposes that data through:

1. An internal Flask dashboard.
2. A versioned REST + GraphQL API at `/api/v1/...` and `/graphql`.
3. An MCP server callable from Claude Desktop / Claude Code.

**v1 is read-only** against the Shopify stores. Write-back operations (order edits, inventory adjustments, refunds) are out of scope and gated behind v2 work.

---

## Source of truth

The full design specification is included in this folder:

> `docs/design_requirements.docx` (v0.1, dated 2026-04-29)

This README and the companion `TECHNICAL_REQUIREMENTS.md` / `BUILD_PLAN.md` files are derived from that spec. When in doubt, the design doc wins.

---

## Architecture in 60 seconds

Three orthogonal axes:

1. **Layered architecture** (5 layers, dependencies flow downward only):
   - **L5 Presentation** — Flask blueprints, MCP tools.
   - **L4 Services** — use-case classes; the only layer routes/MCP/templates may call.
   - **L3 Domain** — pure-Python dataclasses + repository `Protocol`s. No Flask, no SQLAlchemy.
   - **L2 Repositories** — concrete SQLAlchemy implementations of L3 protocols. The **only** layer that imports `sqlalchemy`.
   - **L1 Infrastructure** — DB engine, Shopify client, GA4 + OrderGroove integrations.

2. **Multi-tenancy by row** — every business table carries a non-nullable `store_id`; cross-store reports come for free.

3. **Hybrid sync** — webhooks (near real-time) + on-demand GraphQL (cache miss) + nightly Bulk Operations reconciliation (ground truth).

Layer rules are enforced by `ruff check --select TID` pre-commit hooks plus import-graph tests in `tests/architecture/`. Breaches block CI.

The MCP server runs as a **separate process** but calls the same service classes as the REST API — no parallel data path.

---

## Quickstart (local dev — Phase 1)

```bash
# 1. Clone, copy env, fill in per-store tokens (see Phase 0 discovery)
cp .env.example .env

# 2. Bring up Postgres + Redis
docker compose up postgres redis -d
# (on this dev box we substitute local PG18 on :5432 — see memory)

# 3. Initialize schema
uv run alembic upgrade head

# 4. One-shot bulk import per store (see "Manual sync commands" below for per-resource pulls)
uv run flask sync init
```

The webhook receiver and `flask shopify register-webhooks` are wired but **not in use** — v1 is operating in manual-pull mode (no public tunnel, no webhook subscriptions). When that changes, register webhooks via:

```bash
uv run flask shopify register-webhooks --base-url <tunnel-url>
```

---

## Manual sync commands

v1 runs in manual-pull mode: data freshness equals "whenever you last ran one of these." The `flask sync` group lives in `app/cli.py` and dispatches into `SyncService` (`app/services/sync.py`).

```bash
# Full bulk for every store with real creds in .env (locations + customers + products + inventory + orders)
uv run flask sync init

# Full bulk for one store, with a wider orders window
uv run flask sync init --store lubelife --orders-since-days 30

# Per-resource refresh (one store at a time)
uv run flask sync orders     --store lubelife --since-days 2
uv run flask sync products   --store lubelife --since-days 7
uv run flask sync customers  --store lubelife --since-days 7
uv run flask sync inventory  --store lubelife
uv run flask sync locations  --store lubelife
```

Notes:
- All sync operations are idempotent upserts; safe to re-run.
- `inventory` has no `--since-days` flag — it pulls every inventory item + per-location level for the store.
- `locations` is the same — small set, full pull every time.
- Run `inventory` after `products` and `locations` so foreign keys resolve.

### Nightly drift check (Windows Task Scheduler)

A scheduled task `ShopifyConnectorReconcileDrift` runs `scripts/reconcile_drift_check.cmd` daily at **02:30 local**. It compares webhook-event counts (currently always 0) against a 48-hour bulk re-pull and writes the result to `logs/reconcile_drift_YYYYMMDD_HHMM.log`. While webhooks are off this effectively serves as the nightly orders catch-up sync.

```powershell
# Inspect / change schedule
schtasks /query /tn "ShopifyConnectorReconcileDrift" /v /fo list
schtasks /change /tn "ShopifyConnectorReconcileDrift" /st 03:00

# Run on demand
& "C:\Users\jodom\projects\shopify_connector\scripts\reconcile_drift_check.cmd"
```

---

## Documents in this folder

| File | Purpose |
|---|---|
| `README.md` | This file. |
| `TECHNICAL_REQUIREMENTS.md` | 47 numbered, testable requirements (TR-1 … TR-47). |
| `BUILD_PLAN.md` | 5-phase roadmap with per-phase deliverables and acceptance criteria. |
| `docs/discovery.md` | Phase 0 worksheet — fill in per-store before Phase 1 starts. |
| `docs/custom_app_setup.md` | Click-by-click walkthrough for provisioning Shopify Custom Apps via the Dev Dashboard. |
| `pyproject.toml` | Dependency manifest (uv-managed; Python 3.12+). |
| `.env.example` | Required env vars with placeholder values. |
| `docker-compose.yml` | Local services: postgres 16, redis 7, app, worker, mcp. |
| `Dockerfile` | Multi-entrypoint image used by docker-compose. |
| `alembic.ini` | Migration config. Empty `alembic/versions/` until Phase 1. |
| `scripts/run_discovery_probes.py` | Stdlib-only Phase 0 probe runner. Reads tokens from `.env`, runs locations + orders/30d + products + variants + ShopifyQL `sessions` against all three stores, prints markdown summary. |

---

## Status

- [x] **Phase -1 — Design** (this folder).
- [ ] **Phase 0 — Discovery** (1 week). See `docs/discovery.md`.
- [ ] **Phase 1 — Foundation, domain, repositories** (2–3 weeks).
- [ ] **Phase 2 — Service & serving surfaces** (2–3 weeks).
- [ ] **Phase 3 — Analytics & subscriptions** (2 weeks).
- [ ] **Phase 4 — MCP server** (1–2 weeks).
- [ ] **Phase 5 — Hardening & deploy** (1–2 weeks).
