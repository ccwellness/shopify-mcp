"""Shared pytest fixtures.

The `fake_db` / `fake_uow` / `fake_uow_factory` fixtures wire up the
in-memory persistence layer (`tests.fakes`) so service-layer unit tests
can run with no DB connection at all.

The `test_container` fixture builds a `Container` with the `uow_factory`
and `job_queue` providers overridden to point at the fakes — useful for
integration-shaped tests that exercise `create_app()` end-to-end.

The `valid_token` / `auth_headers` / `authed_client` fixtures (used by
contract tests) mint a real bearer token in the fake database and wire
the test_client to send it on every request.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from dependency_injector import providers
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.container import Container
from app.domain.repositories import UnitOfWork
from app.jobs.queue import InlineJobQueue
from app.services.auth import AuthService
from tests.fakes import InMemoryDatabase, InMemoryUnitOfWork, make_uow_factory


@pytest.fixture
def fake_db() -> InMemoryDatabase:
    return InMemoryDatabase()


@pytest.fixture
def fake_uow(fake_db: InMemoryDatabase) -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(fake_db)


@pytest.fixture
def fake_uow_factory(fake_db: InMemoryDatabase) -> Callable[[], UnitOfWork]:
    return make_uow_factory(fake_db)


@pytest.fixture
def test_container(
    fake_uow_factory: Callable[[], UnitOfWork],
) -> Iterator[Container]:
    """Container with persistence + queue providers overridden for in-process tests."""
    container = Container()
    container.uow_factory.override(providers.Object(fake_uow_factory))
    container.job_queue.override(providers.Singleton(InlineJobQueue))
    container.store_configs.override(providers.Object({}))
    try:
        yield container
    finally:
        container.unwire()
        container.reset_override()


@pytest.fixture
def app(test_container: Container) -> Flask:
    """Flask app built from the test Container — usable by every contract test."""
    return create_app(container=test_container)


@pytest.fixture
def valid_token(fake_uow_factory: Callable[[], UnitOfWork]) -> str:
    """Mint a fresh bearer token in the fake DB and return the plaintext."""
    auth = AuthService(fake_uow_factory)
    _, plaintext = auth.mint(name="contract-tests")
    return plaintext


@pytest.fixture
def auth_headers(valid_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {valid_token}"}


class _AuthedFlaskClient(FlaskClient):
    """Test client that auto-attaches the per-test bearer token."""

    _bearer: str = ""

    def open(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        headers = kwargs.get("headers")
        if isinstance(headers, dict) and "Authorization" not in headers:
            kwargs["headers"] = {
                **headers,
                "Authorization": f"Bearer {self._bearer}",
            }
        elif headers is None:
            kwargs["headers"] = {"Authorization": f"Bearer {self._bearer}"}
        return super().open(*args, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def authed_client(app: Flask, valid_token: str) -> Iterator[FlaskClient]:
    """Flask test_client that attaches a valid bearer on every request."""
    original_class = app.test_client_class

    class _PerTestClient(_AuthedFlaskClient):
        _bearer = valid_token

    app.test_client_class = _PerTestClient
    try:
        with app.test_client() as c:
            yield c
    finally:
        app.test_client_class = original_class


@pytest.fixture
def unauthed_client(app: Flask) -> Iterator[FlaskClient]:
    """Plain test_client with no auth — for negative-path tests."""
    with app.test_client() as c:
        yield c


@pytest.fixture
def dashboard_client(app: Flask, valid_token: str) -> Iterator[FlaskClient]:
    """Test client with an active dashboard session (logged in via API token).

    Posts /login with the minted plaintext, so subsequent requests carry the
    signed session cookie. Reuses `valid_token`'s already-minted ApiToken
    row — one less side-effect to set up per test.
    """
    with app.test_client() as c:
        resp = c.post("/login", data={"token": valid_token})
        assert resp.status_code in (302, 303), resp.get_data(as_text=True)
        yield c
