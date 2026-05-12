"""MCP server entrypoint.

  $ python -m mcp_server              # stdio (Claude Desktop, Claude Code)
  $ python -m mcp_server --http       # HTTP transport behind bearer auth (TR-5)

HTTP mode binds to $MCP_HTTP_HOST (default 127.0.0.1) on $MCP_HTTP_PORT
(default 8801). Pair with a reverse proxy for TLS in shared deployments.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

# Load .env BEFORE importing modules that touch the engine. Phase-1
# pattern, repeated here because the MCP server is its own process.
load_dotenv()

# Tool modules register against `mcp` on import.
import mcp_server.tools  # noqa: E402, F401
from mcp_server.auth import BearerAuthMiddleware  # noqa: E402
from mcp_server.server import mcp  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="mcp_server", description=__doc__)
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve HTTP transport (bearer auth required) instead of stdio.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HTTP_HOST", "127.0.0.1"),
        help="HTTP bind host (default: 127.0.0.1 or $MCP_HTTP_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_HTTP_PORT", "8801")),
        help="HTTP bind port (default: 8801 or $MCP_HTTP_PORT).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.http:
        return _serve_http(host=args.host, port=args.port)
    return _serve_stdio()


def _serve_stdio() -> int:
    """Blocking stdio transport. Used by Claude Desktop / Claude Code."""
    mcp.run()  # FastMCP picks stdio by default when called this way
    return 0


def _serve_http(*, host: str, port: int) -> int:
    """ASGI HTTP transport wrapped in BearerAuthMiddleware (TR-5)."""
    import uvicorn  # noqa: PLC0415 — defer import; stdio path doesn't need it

    app = mcp.http_app()
    secured = BearerAuthMiddleware(app)
    uvicorn.run(secured, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
