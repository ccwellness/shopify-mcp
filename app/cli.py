"""Flask CLI commands.

Registered onto the app via `app.cli.add_command(...)` in `create_app`.

  $ flask sync init                          # sync every store
  $ flask sync init --store lubelife         # one store only
  $ flask sync orders --store lubelife --since-days 2
  $ flask shopify register-webhooks          # register webhooks for every store
  $ flask shopify register-webhooks --store lubelife --base-url https://x.trycloudflare.com
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import click
from flask import current_app
from flask.cli import AppGroup

from app.services.sync import SyncService
from app.shopify import webhook_admin
from app.shopify.client import ShopifyClient

sync_cli = AppGroup("sync", help="Sync data from Shopify into Postgres.")
shopify_cli = AppGroup("shopify", help="Manage Shopify-side resources (webhooks, etc.).")


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
