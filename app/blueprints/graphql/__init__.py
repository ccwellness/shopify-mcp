"""GraphQL gateway blueprint (TR-33).

A single `/graphql` endpoint backed by Strawberry, wrapping the same L4
services as the REST API. Bearer auth + audit middleware are shared with
`/api/*` — auditing records `surface=graphql` so analytics can split by
caller transport.

Why exist alongside REST: REST covers the predictable paginated read
patterns (orders list, low-stock); GraphQL covers exploratory cross-store
queries that benefit from field selection. Both hit the same services so
the dashboard, MCP, and any GraphQL client agree on numbers.
"""

from __future__ import annotations

from flask import Blueprint, request
from strawberry.flask.views import GraphQLView

from app.blueprints.api._middleware import authenticate, make_audit_hook
from app.blueprints.graphql.schema import schema
from app.domain.enums import ApiSurface


def _authenticate_post_only() -> object:
    """Let GET /graphql load GraphiQL unauthenticated; require bearer on POST.

    The IDE HTML/JS itself is static — no data flows on the GET. All actual
    queries go over POST, which still hits the bearer check.
    """
    if request.method == "GET":
        return None
    return authenticate()


bp = Blueprint("graphql", __name__, url_prefix="/graphql")
bp.before_request(_authenticate_post_only)
bp.after_request(make_audit_hook(ApiSurface.GRAPHQL))

bp.add_url_rule(
    "",
    view_func=GraphQLView.as_view("graphql_view", schema=schema, graphql_ide="graphiql"),
)
