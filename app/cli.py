"""Flask CLI commands.

Registered onto the app via `app.cli.add_command(...)` in `create_app`.

  $ flask sync init                          # sync every store
  $ flask sync init --store lubelife         # one store only
  $ flask sync orders --store lubelife --since-days 2
  $ flask shopify register-webhooks          # register webhooks for every store
  $ flask shopify register-webhooks --store lubelife --base-url https://x.trycloudflare.com
  $ flask api mint-token --name=ops-readonly
  $ flask api list-tokens
  $ flask api revoke-token --id=42
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import click
from flask import current_app
from flask.cli import AppGroup

from app.domain.models import ApiTokenId, StoreId
from app.services.analytics import AnalyticsService
from app.services.auth import AuthService
from app.services.sync import SyncService
from app.shopify import webhook_admin
from app.shopify.client import ShopifyClient

sync_cli = AppGroup("sync", help="Sync data from Shopify into Postgres.")
shopify_cli = AppGroup("shopify", help="Manage Shopify-side resources (webhooks, etc.).")
api_cli = AppGroup("api", help="Manage internal API bearer tokens (TR-4).")
analytics_cli = AppGroup("analytics", help="Compute analytics rollups from synced data (TR-31).")


def _service() -> SyncService:
    svc = current_app.extensions.get("sync_service")
    if svc is None:
        raise click.ClickException(
            "sync service is not wired — no store has real creds in .env, "
            "or the app was created without Shopify configs."
        )
    return svc  # type: ignore[no-any-return]


@sync_cli.command("init")
@click.option(
    "--store",
    "store_key",
    default=None,
    help="Sync just one store_key (default: every store with real creds).",
)
@click.option(
    "--orders-since-days",
    default=90,
    type=int,
    show_default=True,
    help="Pull orders updated within this many days back.",
)
def sync_init(store_key: str | None, orders_since_days: int) -> None:
    """Initial bulk import: locations + orders for one or all stores."""
    svc = _service()
    configs = current_app.extensions["store_configs"]
    keys = [store_key] if store_key else sorted(configs.keys())
    if store_key and store_key not in configs:
        raise click.ClickException(f"unknown store_key {store_key!r}")

    since = datetime.now(tz=UTC) - timedelta(days=orders_since_days)
    click.echo(f"orders window: since {since.isoformat()}")

    for key in keys:
        click.secho(f"\n=== {key} ===", fg="cyan", bold=True)
        loc = svc.sync_locations(key)
        click.echo(f"  locations: {loc.upserted} upserted")
        cust = svc.sync_customers(key, since=since)
        click.echo(f"  customers: {cust.upserted} upserted")
        prod = svc.sync_products(key, since=since)
        click.echo(f"  products:  {prod.upserted} upserted")
        # Inventory must run after products + locations so FKs resolve.
        inv = svc.sync_inventory(key)
        click.echo(f"  inventory: {inv.upserted} items upserted")
        order = svc.sync_orders(key, since=since)
        click.echo(f"  orders:    {order.upserted} upserted")
        # Refunds depend on orders being present locally — must run after.
        refunds = svc.sync_refunds(key, since=since)
        click.echo(f"  refunds:   {refunds.upserted} upserted")
        # Sessions are independent of the order/catalog data; pull last N days.
        sessions_days = min(orders_since_days, 30)
        sessions = svc.sync_sessions(key, days_back=sessions_days)
        click.echo(f"  sessions:  {sessions.upserted} sessions_daily rows upserted")
        # Now that sessions + orders are present, roll up the KPI window.
        store_id = _store_id_for_key(key)
        if store_id is not None:
            today = datetime.now(tz=UTC).date()
            kpi = _analytics_service().compute_kpi_window(
                store_id,
                since=today - timedelta(days=sessions_days),
                until=today - timedelta(days=1),
            )
            click.echo(
                f"  analytics: {kpi.days_computed} day(s) computed "
                f"(skipped {kpi.days_skipped_no_sessions} for missing sessions)"
            )


@sync_cli.command("orders")
@click.option("--store", "store_key", required=True)
@click.option("--since-days", default=2, type=int, show_default=True)
def sync_orders_cmd(store_key: str, since_days: int) -> None:
    """Re-sync orders for one store. Useful for nightly reconciliation (TR-15)."""
    svc = _service()
    since = datetime.now(tz=UTC) - timedelta(days=since_days)
    result = svc.sync_orders(store_key, since=since)
    click.echo(f"{result.store_key}: {result.upserted} orders upserted (since {since.date()})")


@sync_cli.command("locations")
@click.option("--store", "store_key", required=True)
def sync_locations_cmd(store_key: str) -> None:
    """Re-sync locations for one store."""
    svc = _service()
    result = svc.sync_locations(store_key)
    click.echo(f"{result.store_key}: {result.upserted} locations upserted")


@sync_cli.command("customers")
@click.option("--store", "store_key", required=True)
@click.option("--since-days", default=2, type=int, show_default=True)
def sync_customers_cmd(store_key: str, since_days: int) -> None:
    """Re-sync customers for one store."""
    svc = _service()
    since = datetime.now(tz=UTC) - timedelta(days=since_days)
    result = svc.sync_customers(store_key, since=since)
    click.echo(f"{result.store_key}: {result.upserted} customers upserted (since {since.date()})")


@sync_cli.command("products")
@click.option("--store", "store_key", required=True)
@click.option("--since-days", default=2, type=int, show_default=True)
def sync_products_cmd(store_key: str, since_days: int) -> None:
    """Re-sync products + variants for one store."""
    svc = _service()
    since = datetime.now(tz=UTC) - timedelta(days=since_days)
    result = svc.sync_products(store_key, since=since)
    click.echo(f"{result.store_key}: {result.upserted} products upserted (since {since.date()})")


@sync_cli.command("inventory")
@click.option("--store", "store_key", required=True)
def sync_inventory_cmd(store_key: str) -> None:
    """Re-sync inventory items + per-location levels for one store."""
    svc = _service()
    result = svc.sync_inventory(store_key)
    click.echo(f"{result.store_key}: {result.upserted} inventory items upserted")


@sync_cli.command("refunds")
@click.option("--store", "store_key", required=True)
@click.option(
    "--since-days",
    default=None,
    type=int,
    help="Only walk orders whose processed_at >= now() - N days (default: all-time).",
)
def sync_refunds_cmd(store_key: str, since_days: int | None) -> None:
    """Pull refunds for every locally-stored refunded order in `store_key`.

    Walks orders with financial_status in (refunded, partially_refunded);
    one GraphQL call per such order. Idempotent — safe to re-run.
    """
    svc = _service()
    since = datetime.now(tz=UTC) - timedelta(days=since_days) if since_days is not None else None
    result = svc.sync_refunds(store_key, since=since)
    suffix = f" (since {since.date()})" if since is not None else ""
    click.echo(f"{result.store_key}: {result.upserted} refunds upserted{suffix}")


@sync_cli.command("sessions")
@click.option("--store", "store_key", required=True)
@click.option(
    "--days",
    "days_back",
    default=30,
    type=int,
    show_default=True,
    help="Pull sessions for the trailing N days (UNTIL -1d, exclusive of today).",
)
def sync_sessions_cmd(store_key: str, days_back: int) -> None:
    """Pull per-day sessions / orders / total_sales via ShopifyQL (TR-29)."""
    svc = _service()
    result = svc.sync_sessions(store_key, days_back=days_back)
    click.echo(
        f"{result.store_key}: {result.upserted} sessions_daily rows upserted (last {days_back}d)"
    )


def _auth_service() -> AuthService:
    svc = current_app.extensions.get("auth_service")
    if svc is None:
        raise click.ClickException("auth_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _analytics_service() -> AnalyticsService:
    svc = current_app.extensions.get("analytics_service")
    if svc is None:
        raise click.ClickException("analytics_service is not wired on this app")
    return svc  # type: ignore[no-any-return]


def _store_id_for_key(store_key: str) -> StoreId | None:
    """Resolve a `store_key` to the numeric `StoreId` via the store-query service.

    Returns None if no active store has that key (typical right after a
    fresh sync_init on a brand-new DB where the store hasn't been
    ensured yet — but `_resolve_store_id` runs as part of `sync_locations`
    so by the time analytics rolls up, the row exists).
    """
    svc = current_app.extensions.get("store_query_service")
    if svc is None:
        return None
    for s in svc.list_active():
        if s.store_key == store_key:
            return s.id
    return None


@analytics_cli.command("compute")
@click.option("--store-id", "store_id_raw", required=True, type=int, help="Store id (numeric).")
@click.option(
    "--since",
    "since_raw",
    required=True,
    help="Start date, inclusive (YYYY-MM-DD).",
)
@click.option(
    "--until",
    "until_raw",
    required=True,
    help="End date, inclusive (YYYY-MM-DD).",
)
def analytics_compute_cmd(store_id_raw: int, since_raw: str, until_raw: str) -> None:
    """Compute analytics_kpi_daily for every day in [since, until] (inclusive)."""
    from datetime import date as _date  # noqa: PLC0415

    try:
        since = _date.fromisoformat(since_raw)
        until = _date.fromisoformat(until_raw)
    except ValueError as exc:
        raise click.ClickException(f"bad date: {exc}") from exc
    svc = _analytics_service()
    result = svc.compute_kpi_window(StoreId(store_id_raw), since=since, until=until)
    click.echo(
        f"store_id={int(result.store_id)}  "
        f"computed={result.days_computed} day(s)  "
        f"skipped(no_sessions)={result.days_skipped_no_sessions}"
    )


@api_cli.command("mint-token")
@click.option("--name", required=True, help="Human-readable label (e.g. 'ops-readonly').")
@click.option(
    "--store-id",
    type=int,
    default=None,
    help="Optional per-store scoping; omit for cross-store access.",
)
@click.option(
    "--expires-days",
    type=int,
    default=None,
    help="Optional expiry in days from now; omit for non-expiring.",
)
def mint_token_cmd(name: str, store_id: int | None, expires_days: int | None) -> None:
    """Mint a new bearer token. Plaintext is printed once and never recoverable."""
    expires_at = (
        datetime.now(tz=UTC) + timedelta(days=expires_days) if expires_days is not None else None
    )
    token, plaintext = _auth_service().mint(
        name=name,
        store_id=StoreId(store_id) if store_id is not None else None,
        expires_at=expires_at,
    )
    click.secho("Token minted. Save the plaintext NOW — it will not be shown again.", fg="yellow")
    click.echo(f"  id:         {int(token.id)}")
    click.echo(f"  name:       {token.name}")
    click.echo(f"  store_id:   {token.store_id}")
    click.echo(f"  expires_at: {token.expires_at}")
    click.secho(f"  TOKEN:      {plaintext}", fg="green", bold=True)


@api_cli.command("list-tokens")
def list_tokens_cmd() -> None:
    """List active (non-revoked) tokens."""
    tokens = _auth_service().list_active()
    if not tokens:
        click.echo("No active tokens.")
        return
    click.echo(f"{'id':<6} {'name':<24} {'store_id':<10} {'expires_at':<28} last_used_at")
    click.echo("-" * 90)
    for t in tokens:
        click.echo(
            f"{int(t.id):<6} {t.name:<24} {str(t.store_id):<10} "
            f"{str(t.expires_at):<28} {t.last_used_at}"
        )


@api_cli.command("revoke-token")
@click.option("--id", "token_id", required=True, type=int, help="Token id from list-tokens.")
def revoke_token_cmd(token_id: int) -> None:
    """Revoke a token by id. Subsequent requests with it return 401."""
    _auth_service().revoke(ApiTokenId(token_id))
    click.echo(f"Token {token_id} revoked.")


def _shopify_client() -> ShopifyClient:
    client = current_app.extensions.get("shopify_client")
    if client is None:
        raise click.ClickException("Shopify client is not wired — no real creds in .env?")
    return client  # type: ignore[no-any-return]


@shopify_cli.command("register-webhooks")
@click.option(
    "--store",
    "store_key",
    default=None,
    help="One store_key (default: every store with real creds).",
)
@click.option(
    "--base-url",
    "base_url",
    default=None,
    help="Public callback base URL (defaults to $WEBHOOK_BASE_URL).",
)
@click.option(
    "--prune",
    is_flag=True,
    default=False,
    help="Also delete webhook subs on this store key whose topics are no longer in our allow-list.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report planned changes without calling create/delete.",
)
def register_webhooks_cmd(
    store_key: str | None,
    base_url: str | None,
    prune: bool,
    dry_run: bool,
) -> None:
    """Reconcile webhook subscriptions on each store with SUBSCRIBED_TOPICS."""
    base_url = base_url or os.environ.get("WEBHOOK_BASE_URL", "")
    if not base_url:
        raise click.ClickException(
            "no callback base URL — pass --base-url or set WEBHOOK_BASE_URL in env."
        )
    if base_url.startswith("https://your-tunnel-subdomain"):
        raise click.ClickException(
            f"WEBHOOK_BASE_URL is still the placeholder ({base_url}). "
            "Set it to a real public HTTPS URL (Cloudflare Tunnel, ngrok, prod domain) first."
        )

    client = _shopify_client()
    configs = current_app.extensions["store_configs"]
    keys = [store_key] if store_key else sorted(configs.keys())
    if store_key and store_key not in configs:
        raise click.ClickException(f"unknown store_key {store_key!r}")

    for key in keys:
        click.secho(f"\n=== {key} ===", fg="cyan", bold=True)
        result = webhook_admin.reconcile(
            client,
            key,
            base_url=base_url,
            prune_unknown=prune,
            dry_run=dry_run,
        )
        prefix = "[DRY-RUN] " if dry_run else ""
        click.echo(f"  {prefix}callback URL:   {result.callback_url}")
        click.echo(f"  {prefix}created:        {len(result.created)}")
        for t in result.created:
            click.echo(f"      + {t}")
        click.echo(f"  {prefix}already wired:  {len(result.already_present)}")
        click.echo(f"  {prefix}relocated:      {len(result.relocated)}")
        for t in result.relocated:
            click.echo(f"      ~ {t}")
        if prune:
            click.echo(f"  {prefix}pruned:         {len(result.deleted)}")
            for t in result.deleted:
                click.echo(f"      - {t}")
