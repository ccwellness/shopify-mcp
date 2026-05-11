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
)

from app.blueprints.dashboard import views  # noqa: E402, F401 — registers routes
