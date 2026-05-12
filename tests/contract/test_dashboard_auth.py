"""Tests for dashboard session auth (login / logout / gate).

The gate lives on `app.blueprints.dashboard.views._require_login` and
keys off `session['token_id']`. Login validates a bearer token via
AuthService and mints a fresh signed session.
"""

from __future__ import annotations

from http import HTTPStatus

from flask.testing import FlaskClient

# ---------------------------------------------------------------------------
# /login GET + POST
# ---------------------------------------------------------------------------


def test_login_page_renders(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.get("/login")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "Sign in" in body
    assert "API token" in body


def test_login_accepts_valid_token_and_redirects_home(
    unauthed_client: FlaskClient, valid_token: str
) -> None:
    resp = unauthed_client.post("/login", data={"token": valid_token})
    assert resp.status_code == HTTPStatus.SEE_OTHER
    assert resp.headers["Location"].rstrip("/").endswith("")
    # After login, the gated home page returns 200 not 302.
    resp2 = unauthed_client.get("/")
    assert resp2.status_code == HTTPStatus.OK


def test_login_rejects_blank_token(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.post("/login", data={"token": ""}, follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "token is required" in body


def test_login_rejects_invalid_token(unauthed_client: FlaskClient) -> None:
    resp = unauthed_client.post("/login", data={"token": "ccsc_bogus_value"}, follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "invalid or expired token" in body


def test_login_redirects_to_next_when_safe(unauthed_client: FlaskClient, valid_token: str) -> None:
    resp = unauthed_client.post("/login", data={"token": valid_token, "next": "/compare"})
    assert resp.status_code == HTTPStatus.SEE_OTHER
    assert resp.headers["Location"].endswith("/compare")


def test_login_rejects_open_redirect_next(unauthed_client: FlaskClient, valid_token: str) -> None:
    # `next=//evil.example.com` would otherwise become `Location: //evil.example.com`
    # and the browser would honor it as a protocol-relative redirect.
    resp = unauthed_client.post(
        "/login", data={"token": valid_token, "next": "//evil.example.com/x"}
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
    location = resp.headers["Location"]
    assert "evil.example.com" not in location


def test_login_rejects_absolute_url_next(unauthed_client: FlaskClient, valid_token: str) -> None:
    resp = unauthed_client.post(
        "/login", data={"token": valid_token, "next": "https://evil.example.com/x"}
    )
    location = resp.headers["Location"]
    assert "evil.example.com" not in location


# ---------------------------------------------------------------------------
# Gate behavior on /
# ---------------------------------------------------------------------------


def test_gate_preserves_next_url(unauthed_client: FlaskClient) -> None:
    # GET /compare while unauthenticated → 302 to /login?next=/compare?...
    resp = unauthed_client.get("/compare?since=2026-05-01T00:00:00Z&until=2026-05-08T00:00:00Z")
    assert resp.status_code == HTTPStatus.FOUND
    assert "next=" in resp.headers["Location"]
    assert "compare" in resp.headers["Location"]


def test_logout_clears_session(unauthed_client: FlaskClient, valid_token: str) -> None:
    unauthed_client.post("/login", data={"token": valid_token})
    # Home reachable.
    assert unauthed_client.get("/").status_code == HTTPStatus.OK
    # After logout, home redirects back to /login.
    unauthed_client.post("/logout")
    resp = unauthed_client.get("/")
    assert resp.status_code == HTTPStatus.FOUND
    assert "/login" in resp.headers["Location"]


def test_login_when_already_signed_in_bounces_to_next(
    unauthed_client: FlaskClient, valid_token: str
) -> None:
    unauthed_client.post("/login", data={"token": valid_token})
    resp = unauthed_client.get("/login?next=/orders")
    assert resp.status_code == HTTPStatus.FOUND
    assert resp.headers["Location"].endswith("/orders")
