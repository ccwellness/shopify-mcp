"""REST API v1 — read-only endpoints exposing the L4 services."""

from __future__ import annotations

from flask import Blueprint

from app.blueprints.api.v1 import compare, inventory, orders

bp = Blueprint("api_v1", __name__, url_prefix="/v1")
bp.register_blueprint(orders.bp)
bp.register_blueprint(inventory.bp)
bp.register_blueprint(compare.bp)
