"""Bearer auth (before_request) + audit logging (after_request) for /api/*.

Wired onto `app.blueprints.api.bp` in that package's `__init__.py`. Both
hooks read the route's auth_service / audit_service from
`current_app.extensions`. The Container assembles those at app-build time.

Design notes:
- The audit row is written even on 401, so failed-auth attempts are
  recorded with caller_identity='anonymous'. (TR-6 — every API call.)
- Latency uses `time.monotonic()` because wall-clock can jump.
- An audit-write failure must NOT mask the original response. The hook
  catches and logs; the response goes back unchanged.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from flask import Response, current_app, g, jsonify, request

from app.domain.enums import ApiSurface
from app.services.audit import AuditService
from app.services.auth import AuthService

_log = logging.getLogger(__name__)
_REQUEST_START_KEY = "_api_request_start"


def _services() -> tuple[AuthService | None, AuditService | None]:
    return (
        current_app.extensions.get("auth_service"),
        current_app.extensions.get("audit_service"),
    )


def authenticate() -> tuple[Response, int] | None:
    """before_request: enforce bearer auth on every /api/* route."""
    g.api_token = None
    g.setdefault(_REQUEST_START_KEY, time.monotonic())

    auth_service, _ = _services()
    if auth_service is None:
        # Wiring error — fail closed.
        return jsonify({"error": "auth not wired"}), int(HTTPStatus.SERVICE_UNAVAILABLE)

    raw = request.headers.get("Authorization", "")
    if not raw.startswith("Bearer "):
        return jsonify({"error": "missing bearer token"}), int(HTTPStatus.UNAUTHORIZED)
    plaintext = raw.removeprefix("Bearer ").strip()

    token = auth_service.validate(plaintext)
    if token is None:
        return jsonify({"error": "invalid or expired token"}), int(HTTPStatus.UNAUTHORIZED)

    g.api_token = token
    return None


def audit(response: Response) -> Response:
    """after_request: write one row to api_audit_log per call (TR-6)."""
    _, audit_service = _services()
    if audit_service is None:
        return response

    start = g.get(_REQUEST_START_KEY)
    latency_ms = int((time.monotonic() - start) * 1000) if start else None
    token = g.get("api_token")
    caller_identity = token.name if token else "anonymous"
    store_id = token.store_id if token else None
    params: dict[str, Any] = {k: request.args.getlist(k) for k in request.args}

    try:
        audit_service.record(
            caller_identity=caller_identity,
            store_id=store_id,
            surface=ApiSurface.REST.value,
            route_or_tool=request.path,
            params=params,
            status_code=response.status_code,
            latency_ms=latency_ms,
            request_id=request.headers.get("X-Request-Id"),
            ts=datetime.now(tz=UTC),
        )
    except Exception:  # noqa: BLE001 — audit must never break the response
        _log.exception("api_audit_log write failed for %s", request.path)
    return response
