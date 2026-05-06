"""Contract tests for /api/* bearer auth + audit log middleware (TR-4, TR-6)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

from flask.testing import FlaskClient

from app.domain.repositories import UnitOfWork
from app.services.auth import AuthService

# ---------------------------------------------------------------------------
# Auth: missing / bad / revoked / expired
# ---------------------------------------------------------------------------


def test_missing_authorization_header_returns_401(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.get("/api/v1/orders")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED
    assert "missing bearer token" in resp.get_json()["error"]


def test_non_bearer_authorization_returns_401(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.get("/api/v1/orders", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_garbage_bearer_returns_401(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.get(
        "/api/v1/orders", headers={"Authorization": "Bearer ccsc_not-a-real-token"}
    )
    assert resp.status_code == HTTPStatus.UNAUTHORIZED
    assert "invalid or expired token" in resp.get_json()["error"]


def test_valid_bearer_returns_200(authed_client: FlaskClient) -> None:
    resp = authed_client.get("/api/v1/orders")
    assert resp.status_code == HTTPStatus.OK


def test_revoked_token_returns_401(
    unauthed_client: FlaskClient,
    fake_uow_factory: Callable[[], UnitOfWork],
) -> None:
    auth = AuthService(fake_uow_factory)
    token, plaintext = auth.mint(name="will-be-revoked")
    auth.revoke(token.id)
    resp = unauthed_client.get("/api/v1/orders", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_expired_token_returns_401(
    unauthed_client: FlaskClient,
    fake_uow_factory: Callable[[], UnitOfWork],
) -> None:
    auth = AuthService(fake_uow_factory)
    _, plaintext = auth.mint(name="expired", expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    resp = unauthed_client.get("/api/v1/orders", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


# ---------------------------------------------------------------------------
# Audit log: written for both success and failed-auth paths
# ---------------------------------------------------------------------------


def test_audit_log_written_for_authed_call(
    authed_client: FlaskClient, fake_uow: UnitOfWork
) -> None:
    authed_client.get("/api/v1/orders?store_id=1")
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert len(rows) == 1
    entry = rows[0]
    assert entry.caller_identity == "contract-tests"
    assert entry.route_or_tool == "/api/v1/orders"
    assert entry.status_code == HTTPStatus.OK
    assert entry.surface == "rest"
    assert entry.params_sanitized == {"store_id": ["1"]}
    assert entry.latency_ms is not None
    assert entry.latency_ms >= 0


def test_audit_log_written_for_failed_auth(
    unauthed_client: FlaskClient, fake_uow: UnitOfWork
) -> None:
    unauthed_client.get("/api/v1/orders")
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert len(rows) == 1
    entry = rows[0]
    assert entry.caller_identity == "anonymous"
    assert entry.status_code == HTTPStatus.UNAUTHORIZED


def test_audit_log_records_request_id_when_present(
    authed_client: FlaskClient, fake_uow: UnitOfWork
) -> None:
    authed_client.get("/api/v1/orders", headers={"X-Request-Id": "req-123"})
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert rows[0].request_id == "req-123"


def test_validate_touches_last_used_at(
    fake_uow_factory: Callable[[], UnitOfWork],
    fake_uow: UnitOfWork,
) -> None:
    auth = AuthService(fake_uow_factory)
    _, plaintext = auth.mint(name="ops")
    with fake_uow as uow:
        before = uow.api_tokens.list_active()[0].last_used_at
    auth.validate(plaintext)
    with fake_uow as uow:
        after = uow.api_tokens.list_active()[0].last_used_at
    assert before is None
    assert after is not None
