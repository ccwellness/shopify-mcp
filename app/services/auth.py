"""AuthService — bearer-token mint + validate (TR-4).

Plaintext tokens have the format `ccsc_<24 random url-safe bytes>` (~32 chars,
~192 bits of entropy). Storage holds the SHA-256 hex digest, never the
plaintext. Validation rehashes, looks up, and checks the revoked/expired
flags.

`hashlib.sha256` (not bcrypt) is fine for bearer tokens because the
plaintext is high-entropy random; brute force isn't feasible. bcrypt is
for low-entropy human passwords.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from app.domain.models import ApiToken, ApiTokenId, StoreId
from app.domain.repositories import UnitOfWork

TOKEN_PREFIX = "ccsc_"  # noqa: S105 — visible-token namespace prefix, not a secret
TOKEN_BYTES = 24


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _generate_plaintext() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(TOKEN_BYTES)}"


class AuthService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def mint(
        self,
        *,
        name: str,
        store_id: StoreId | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[ApiToken, str]:
        """Mint a new token. Returns (persisted_token, plaintext).

        The plaintext is only available here — store it on the operator side.
        """
        plaintext = _generate_plaintext()
        token_hash = _hash(plaintext)
        now = datetime.now(tz=UTC)
        candidate = ApiToken(
            id=ApiTokenId(0),  # placeholder; repo assigns the real id on insert
            name=name,
            token_hash=token_hash,
            store_id=store_id,
            created_at=now,
            expires_at=expires_at,
            revoked_at=None,
            last_used_at=None,
        )
        with self._uow_factory() as uow:
            new_id = uow.api_tokens.upsert(candidate)
            uow.commit()
        return (
            ApiToken(
                id=new_id,
                name=name,
                token_hash=token_hash,
                store_id=store_id,
                created_at=now,
                expires_at=expires_at,
                revoked_at=None,
                last_used_at=None,
            ),
            plaintext,
        )

    def validate(self, plaintext: str) -> ApiToken | None:
        """Return the matching token or None. Touches `last_used_at` on success."""
        if not plaintext:
            return None
        token_hash = _hash(plaintext)
        now = datetime.now(tz=UTC)
        with self._uow_factory() as uow:
            token = uow.api_tokens.get_by_hash(token_hash)
            if token is None:
                return None
            # Constant-time hash compare even though the lookup already did
            # an equality check — defence in depth against any future change.
            if not hmac.compare_digest(token.token_hash, token_hash):
                return None
            if token.revoked_at is not None:
                return None
            if token.expires_at is not None and token.expires_at < now:
                return None
            uow.api_tokens.touch_last_used(token.id, now)
            uow.commit()
            # Return the post-touch view so callers see the freshly stamped
            # last_used_at without needing a re-fetch.
            return ApiToken(
                id=token.id,
                name=token.name,
                token_hash=token.token_hash,
                store_id=token.store_id,
                created_at=token.created_at,
                expires_at=token.expires_at,
                revoked_at=token.revoked_at,
                last_used_at=now,
            )

    def revoke(self, token_id: ApiTokenId) -> None:
        with self._uow_factory() as uow:
            uow.api_tokens.revoke(token_id, datetime.now(tz=UTC))
            uow.commit()

    def list_active(self) -> tuple[ApiToken, ...]:
        with self._uow_factory() as uow:
            return uow.api_tokens.list_active()
