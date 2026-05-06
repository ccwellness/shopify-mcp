"""SqlAlchemyApiTokenRepository — bearer-token store backed by `api_tokens`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.orm.api_token import ApiTokenRow
from app.domain.models import ApiToken, ApiTokenId, StoreId


def _to_domain(row: ApiTokenRow) -> ApiToken:
    return ApiToken(
        id=ApiTokenId(row.id),
        name=row.name,
        token_hash=row.token_hash,
        store_id=StoreId(row.store_id) if row.store_id is not None else None,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
    )


class SqlAlchemyApiTokenRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_hash(self, token_hash: str) -> ApiToken | None:
        row = self._session.scalar(select(ApiTokenRow).where(ApiTokenRow.token_hash == token_hash))
        return _to_domain(row) if row is not None else None

    def list_active(self) -> tuple[ApiToken, ...]:
        rows = self._session.scalars(
            select(ApiTokenRow).where(ApiTokenRow.revoked_at.is_(None))
        ).all()
        return tuple(_to_domain(r) for r in rows)

    def upsert(self, token: ApiToken) -> ApiTokenId:
        row = self._session.scalar(
            select(ApiTokenRow).where(ApiTokenRow.token_hash == token.token_hash)
        )
        if row is None:
            row = ApiTokenRow(
                name=token.name,
                token_hash=token.token_hash,
                store_id=int(token.store_id) if token.store_id is not None else None,
                expires_at=token.expires_at,
                revoked_at=token.revoked_at,
                last_used_at=token.last_used_at,
            )
            self._session.add(row)
            self._session.flush()
        else:
            row.name = token.name
            row.store_id = int(token.store_id) if token.store_id is not None else None
            row.expires_at = token.expires_at
            row.revoked_at = token.revoked_at
            row.last_used_at = token.last_used_at
            self._session.flush()
        return ApiTokenId(row.id)

    def touch_last_used(self, token_id: ApiTokenId, when: datetime) -> None:
        row = self._session.get(ApiTokenRow, int(token_id))
        if row is not None:
            row.last_used_at = when
            self._session.flush()

    def revoke(self, token_id: ApiTokenId, when: datetime) -> None:
        row = self._session.get(ApiTokenRow, int(token_id))
        if row is not None:
            row.revoked_at = when
            self._session.flush()
