"""Shared pytest fixtures.

The `fake_db` / `fake_uow` / `fake_uow_factory` fixtures wire up the
in-memory persistence layer (`tests.fakes`) so service-layer unit tests
can run with no DB connection at all.

The `test_container` fixture builds a `Container` with the `uow_factory`
and `job_queue` providers overridden to point at the fakes — useful for
integration-shaped tests that exercise `create_app()` end-to-end.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from dependency_injector import providers

from app.container import Container
from app.domain.repositories import UnitOfWork
from app.jobs.queue import InlineJobQueue
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
