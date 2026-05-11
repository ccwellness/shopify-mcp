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

from flask import Blueprint
from strawberry.flask.views import GraphQLView

from app.blueprints.api._middleware import authenticate, make_audit_hook
from app.blueprints.graphql.schema import schema
from app.domain.enums import ApiSurface

bp = Blueprint("graphql", __name__, url_prefix="/graphql")
bp.before_request(authenticate)
bp.after_request(make_audit_hook(ApiSurface.GRAPHQL))

bp.add_url_rule(
    "",
    view_func=GraphQLView.as_view("graphql_view", schema=schema, graphql_ide="graphiql"),
)
