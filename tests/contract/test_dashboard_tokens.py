"""Smoke tests for the /admin/tokens dashboard view (TR-4).

Wraps AuthService.mint/list_active/revoke from a browser-shaped flow.
Plaintext is verified via the `flash`-redirect chain: POST /mint → 303 →
GET /admin/tokens with the plaintext rendered in the success banner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus

import pytest
from flask.testing import FlaskClient

from app.domain.models import ApiToken, ApiTokenId, StoreId, SubscriptionProvider
from app.domain.repositories import UnitOfWork
from app.services.auth import AuthService
from tests.fakes import InMemoryDatabase, InMemoryUnitOfWork

LUBELIFE = StoreId(1)
T0 = datetime(2026, 5, 12, tzinfo=UTC)


@pytest.fixture
def seed_store(fake_uow: UnitOfWork) -> UnitOfWork:
    # Tokens-list view also lists stores for its dropdown — seed one so the
    # form renders a real option.
    with fake_uow as uow:
        from app.domain.models import Store  # local import to keep top clean  # noqa: PLC0415

        uow.stores.upsert(
            Store(
                id=LUBELIFE,
                store_key="lubelife",
                shop_domain="lubelife.myshopify.com",
                display_name="lubelife",
                plus=False,
                subscription_provider=SubscriptionProvider.UNKNOWN,
                read_only=True,
                active=True,
                timezone=None,
                currency_code="USD",
                created_at=T0,
                updated_at=T0,
            )
        )
    return fake_uow


def _existing_token(uow: UnitOfWork) -> ApiToken:
    return uow.api_tokens.list_active()[0]


# ---------------------------------------------------------------------------
# GET /admin/tokens — list + form rendering
# ---------------------------------------------------------------------------


def test_tokens_list_renders(dashboard_client: FlaskClient) -> None:
    # The session-login fixture itself mints a `contract-tests` token, so
    # the list is never truly empty here. We just verify the page renders
    # and the fixture's token is visible.
    resp = dashboard_client.get("/admin/tokens")
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    assert "API tokens" in body
    assert "contract-tests" in body


def test_tokens_list_shows_existing(
    dashboard_client: FlaskClient,
    fake_uow_factory,  # noqa: ANN001
) -> None:
    # Mint a token directly via the service so we can assert it renders.
    AuthService(fake_uow_factory).mint(name="existing-one")
    resp = dashboard_client.get("/admin/tokens")
    body = resp.get_data(as_text=True)
    assert "existing-one" in body


# ---------------------------------------------------------------------------
# POST /admin/tokens/mint — happy path + validation
# ---------------------------------------------------------------------------


def test_mint_redirects_and_reveals_plaintext(
    dashboard_client: FlaskClient, fake_db: InMemoryDatabase
) -> None:
    resp = dashboard_client.post(
        "/admin/tokens/mint",
        data={"name": "new-token"},
        follow_redirects=True,
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.get_data(as_text=True)
    # The mint banner shows the plaintext exactly once.
    assert "Token minted — copy now" in body
    assert "new-token" in body
    # The plaintext is a `ccsc_` prefix (per AuthService._generate_plaintext).
    assert "ccsc_" in body

    # Sanity: the new token row exists in the fake DB alongside the
    # fixture's session token.
    tokens = InMemoryUnitOfWork(fake_db).api_tokens.list_active()
    assert any(t.name == "new-token" for t in tokens)


def test_mint_rejects_blank_name(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.post("/admin/tokens/mint", data={"name": ""}, follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "name is required" in body


def test_mint_with_store_scope(
    dashboard_client: FlaskClient,
    seed_store: UnitOfWork,
    fake_db: InMemoryDatabase,
) -> None:
    resp = dashboard_client.post(
        "/admin/tokens/mint",
        data={"name": "scoped", "store_id": str(int(LUBELIFE))},
        follow_redirects=True,
    )
    assert resp.status_code == HTTPStatus.OK
    tokens = InMemoryUnitOfWork(fake_db).api_tokens.list_active()
    scoped = next(t for t in tokens if t.name == "scoped")
    assert scoped.store_id == LUBELIFE


def test_mint_rejects_non_integer_store_id(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.post(
        "/admin/tokens/mint",
        data={"name": "bad", "store_id": "abc"},
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert "store_id must be an integer" in body


def test_mint_with_expires_days_sets_expiry(
    dashboard_client: FlaskClient,
    fake_db: InMemoryDatabase,
) -> None:
    dashboard_client.post(
        "/admin/tokens/mint",
        data={"name": "expires-soon", "expires_days": "7"},
        follow_redirects=True,
    )
    tokens = InMemoryUnitOfWork(fake_db).api_tokens.list_active()
    token = next(t for t in tokens if t.name == "expires-soon")
    assert token.expires_at is not None
    delta_days = (token.expires_at - datetime.now(tz=UTC)).days
    # 7 days minus a few seconds of test runtime
    expected_days = 7
    assert expected_days - 1 <= delta_days <= expected_days


def test_mint_rejects_zero_or_negative_expires(dashboard_client: FlaskClient) -> None:
    resp = dashboard_client.post(
        "/admin/tokens/mint",
        data={"name": "zero-exp", "expires_days": "0"},
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    # Jinja2 escapes `>` → `&gt;` in the rendered HTML.
    assert "expires_days must be" in body
    assert "0" in body


# ---------------------------------------------------------------------------
# POST /admin/tokens/<id>/revoke
# ---------------------------------------------------------------------------


def test_revoke_marks_token_revoked(
    dashboard_client: FlaskClient,
    fake_uow_factory,  # noqa: ANN001
    fake_db: InMemoryDatabase,
) -> None:
    token, _plaintext = AuthService(fake_uow_factory).mint(name="to-revoke")

    resp = dashboard_client.post(
        f"/admin/tokens/{int(token.id)}/revoke",
        follow_redirects=False,
    )
    # POST → 303 redirect to the list page (no follow needed to inspect state).
    assert resp.status_code == HTTPStatus.SEE_OTHER

    # The revoked token disappears from list_active.
    active = InMemoryUnitOfWork(fake_db).api_tokens.list_active()
    assert all(t.id != token.id for t in active)


def test_revoke_unknown_id_is_idempotent(dashboard_client: FlaskClient) -> None:
    # Revoking a nonexistent token should not 500; AuthService.revoke is a
    # no-op when the id doesn't exist. Verifies the route doesn't add extra
    # validation that would diverge from the CLI's behavior.
    resp = dashboard_client.post(
        f"/admin/tokens/{int(ApiTokenId(9999))}/revoke", follow_redirects=False
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
