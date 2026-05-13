"""Internal dashboard blueprint (TR-32).

Server-rendered Jinja2 templates over Pico CSS + a sprinkle of HTMX.
v1 is local-only — no auth on these routes — so it must not be exposed
publicly until session auth is wired. Mounted at root so `/` and
`/orders`, `/compare`, `/inventory/low-stock` are the user-visible paths.
"""

from __future__ import annotations

from flask import Blueprint

bp = Blueprint(
    "dashboard",
    __name__,
    template_folder="templates",
    static_folder="static",
    # Explicit url path so it doesn't collide with Flask's app-level /static
    # (the dashboard blueprint is mounted at root, so the default endpoint
    # would conflict).
    static_url_path="/dashboard-static",
)

from app.blueprints.dashboard import filters, views  # noqa: E402, F401 — registers routes + filters

filters.register(bp)
