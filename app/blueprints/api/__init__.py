"""Versioned REST API blueprints (TR-32).

`api_bp` aggregates every versioned sub-blueprint and wires the bearer
auth + audit-log middleware (TR-4, TR-6). Mount it once in `create_app()`
to expose `/api/v1/...` routes.
"""

from __future__ import annotations

from flask import Blueprint

from app.blueprints.api._middleware import audit, authenticate
from app.blueprints.api.v1 import bp as v1_bp

bp = Blueprint("api", __name__, url_prefix="/api")
bp.before_request(authenticate)
bp.after_request(audit)
bp.register_blueprint(v1_bp)
