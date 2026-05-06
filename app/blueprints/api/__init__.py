"""Versioned REST API blueprints (TR-32).

`api_bp` aggregates every versioned sub-blueprint. Mount it once in
`create_app()` to expose `/api/v1/...` routes.
"""

from __future__ import annotations

from flask import Blueprint

from app.blueprints.api.v1 import bp as v1_bp

bp = Blueprint("api", __name__, url_prefix="/api")
bp.register_blueprint(v1_bp)
