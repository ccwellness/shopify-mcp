"""Flask app factory.

Builds (or accepts) a `Container` and pulls fully-wired services from it.
Tests pass in a pre-configured container with provider overrides to
substitute `InMemory*` fakes for the persistence and queue layers.
"""

from __future__ import annotations

import os

from flask import Flask

from app.blueprints.api import bp as api_bp
from app.blueprints.webhooks import bp as webhooks_bp
from app.cli import shopify_cli, sync_cli
from app.container import Container


def create_app(*, container: Container | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-only-not-for-production")

    container = container or Container()
    configs = container.store_configs()

    app.extensions["container"] = container
    app.extensions["store_configs"] = configs
    app.extensions["job_queue"] = container.job_queue()
    app.extensions["webhook_ingest"] = container.webhook_ingest_service()
    app.extensions["order_query_service"] = container.order_query_service()

    # Shopify-facing services are only wired if at least one store has real
    # creds — keeps tests / dev-without-`.env` paths working.
    if configs:
        app.extensions["shopify_client"] = container.shopify_client()
        app.extensions["bulk_client"] = container.bulk_client()
        app.extensions["sync_service"] = container.sync_service()

    app.register_blueprint(webhooks_bp)
    app.register_blueprint(api_bp)
    app.cli.add_command(sync_cli)
    app.cli.add_command(shopify_cli)
    return app
