"""MCP server — read-only tools backed by the same L4 services as REST + GraphQL.

The MCP server runs as its own Python process (stdio for Claude Desktop /
Claude Code, HTTP for shared deployments). All tool functions live in
`mcp_server.tools`; they dispatch into `app.services.*` via a lazy-built
`Container`. No tool may import `app.db`, `app.repositories`, or `sqlalchemy`
directly — the architecture tests enforce this.

Entrypoints:

  $ python -m mcp_server            # stdio (Claude Desktop / Claude Code)
  $ python -m mcp_server --http     # HTTP on $MCP_HTTP_HOST:$MCP_HTTP_PORT
"""

from __future__ import annotations
