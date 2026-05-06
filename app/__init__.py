"""Flask app factory.

Minimal for now — wires the webhook receiver to the DB. The DI container
will own this once `app/container.py` lands; until then the wiring lives
inline here so the receiver can be exercised end-to-end.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from flask import Flask

from app.blueprints.webhooks import bp as webhooks_bp
from app.cli import shopify_cli, sync_cli
from app.db.engine import get_session_factory
from app.db.unit_of_work import SqlAlchemyUnitOfWork
from app.domain.repositories import UnitOfWork
from app.jobs.queue import InlineJobQueue, JobQueue
from app.services.sync import SyncService
from app.services.webhook_ingest import WebhookIngestService
from app.shopify.bulk import BulkOperationsClient
from app.shopify.client import ShopifyClient
from app.shopify.config import load_store_configs


def create_app(*, job_queue: JobQueue | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-only-not-for-production")

    configs = load_store_configs()

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(get_session_factory())

    typed_uow_factory: Callable[[], UnitOfWork] = uow_factory

    # Default to InlineJobQueue for now — RQ wiring requires Redis, which
    # isn't available on this dev box. Production wiring would substitute
    # an `RqJobQueue(redis_url=os.environ["REDIS_URL"])` here.
    queue: JobQueue = job_queue or InlineJobQueue()

    app.extensions["store_configs"] = configs
    app.extensions["job_queue"] = queue
    app.extensions["webhook_ingest"] = WebhookIngestService(typed_uow_factory, configs, queue)

    # Shopify-facing services are only wired if at least one store has real
    # creds — keeps tests / dev-without-`.env` paths working.
    if configs:
        shopify_client = ShopifyClient(configs)
        bulk_client = BulkOperationsClient(shopify_client)
        app.extensions["shopify_client"] = shopify_client
        app.extensions["bulk_client"] = bulk_client
        app.extensions["sync_service"] = SyncService(
            typed_uow_factory, shopify_client, bulk_client, configs
        )

    app.register_blueprint(webhooks_bp)
    app.cli.add_command(sync_cli)
    app.cli.add_command(shopify_cli)
    return app
