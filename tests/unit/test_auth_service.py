"""Unit tests for AuthService — mint/validate/revoke (TR-4)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.repositories import UnitOfWork
from app.services.auth import TOKEN_PREFIX, AuthService


@pytest.fixture
def auth(fake_uow_factory: Callable[[], UnitOfWork]) -> AuthService:
    return AuthService(fake_uow_factory)


def test_mint_returns_prefixed_plaintext_and_persisted_token(auth: AuthService) -> None:
    token, plaintext = auth.mint(name="ops")
    assert plaintext.startswith(TOKEN_PREFIX)
    assert token.name == "ops"
    assert token.revoked_at is None
    assert int(token.id) > 0


def test_mint_persists_only_the_hash(auth: AuthService, fake_uow: UnitOfWork) -> None:
    _, plaintext = auth.mint(name="ops")
    with fake_uow as uow:
        active = uow.api_tokens.list_active()
    assert len(active) == 1
    # Stored hash must not equal plaintext.
    assert active[0].token_hash != plaintext
    # And nobody persisted the plaintext under any field we can see.
    for stored in active:
        assert plaintext not in stored.token_hash


def test_validate_accepts_freshly_minted_plaintext(auth: AuthService) -> None:
    token, plaintext = auth.mint(name="ops")
    found = auth.validate(plaintext)
    assert found is not None
    assert found.id == token.id
    assert found.last_used_at is not None  # touched


def test_validate_rejects_unknown_token(auth: AuthService) -> None:
    assert auth.validate(f"{TOKEN_PREFIX}not-a-real-token") is None


def test_validate_rejects_empty(auth: AuthService) -> None:
    assert auth.validate("") is None


def test_validate_rejects_revoked(auth: AuthService) -> None:
    token, plaintext = auth.mint(name="ops")
    auth.revoke(token.id)
    assert auth.validate(plaintext) is None


def test_validate_rejects_expired(
    auth: AuthService, fake_uow_factory: Callable[[], UnitOfWork]
) -> None:
    past = datetime.now(tz=UTC) - timedelta(days=1)
    _, plaintext = auth.mint(name="expired", expires_at=past)
    assert auth.validate(plaintext) is None


def test_validate_accepts_future_expiry(auth: AuthService) -> None:
    future = datetime.now(tz=UTC) + timedelta(days=30)
    _, plaintext = auth.mint(name="ok", expires_at=future)
    assert auth.validate(plaintext) is not None


def test_list_active_excludes_revoked(auth: AuthService) -> None:
    keep, _ = auth.mint(name="keep")
    drop, _ = auth.mint(name="drop")
    auth.revoke(drop.id)
    active = auth.list_active()
    names = {t.name for t in active}
    assert "keep" in names
    assert "drop" not in names
    assert keep.id in {t.id for t in active}
