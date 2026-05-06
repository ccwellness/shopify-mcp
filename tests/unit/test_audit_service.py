"""Unit tests for AuditService — append + sanitize (TR-6)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from app.domain.enums import ApiSurface
from app.domain.models import StoreId
from app.domain.repositories import UnitOfWork
from app.services.audit import AuditService


@pytest.fixture
def audit(fake_uow_factory: Callable[[], UnitOfWork]) -> AuditService:
    return AuditService(fake_uow_factory)


def test_record_appends_one_row(audit: AuditService, fake_uow: UnitOfWork) -> None:
    EXPECTED_STATUS = 200
    EXPECTED_LATENCY_MS = 12
    audit.record(
        caller_identity="ops",
        store_id=StoreId(1),
        surface=ApiSurface.REST.value,
        route_or_tool="/api/v1/orders",
        params={"store_id": ["1"]},
        status_code=EXPECTED_STATUS,
        latency_ms=EXPECTED_LATENCY_MS,
        request_id="req-abc",
        ts=datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
    )
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent(limit=10)
    assert len(rows) == 1
    entry = rows[0]
    assert entry.caller_identity == "ops"
    assert entry.surface == "rest"
    assert entry.route_or_tool == "/api/v1/orders"
    assert entry.status_code == EXPECTED_STATUS
    assert entry.latency_ms == EXPECTED_LATENCY_MS


def test_record_sanitizes_redacted_params(audit: AuditService, fake_uow: UnitOfWork) -> None:
    audit.record(
        caller_identity="ops",
        store_id=None,
        surface=ApiSurface.REST.value,
        route_or_tool="/api/v1/whatever",
        params={"password": ["hunter2"], "token": ["x"], "store_id": ["1"]},
        status_code=200,
        latency_ms=5,
        request_id=None,
    )
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert rows[0].params_sanitized == {
        "password": "[REDACTED]",
        "token": "[REDACTED]",
        "store_id": ["1"],
    }


def test_record_handles_no_params(audit: AuditService, fake_uow: UnitOfWork) -> None:
    audit.record(
        caller_identity="ops",
        store_id=None,
        surface=ApiSurface.REST.value,
        route_or_tool="/api/v1/whatever",
        params=None,
        status_code=204,
        latency_ms=1,
        request_id=None,
    )
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent()
    assert rows[0].params_sanitized is None


def test_list_recent_returns_newest_first(audit: AuditService, fake_uow: UnitOfWork) -> None:
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    for i in range(3):
        audit.record(
            caller_identity=f"caller-{i}",
            store_id=None,
            surface=ApiSurface.REST.value,
            route_or_tool=f"/api/v1/r{i}",
            params=None,
            status_code=200,
            latency_ms=i,
            request_id=None,
            ts=base.replace(second=i),
        )
    with fake_uow as uow:
        rows = uow.api_audit_log.list_recent(limit=2)
    assert [r.caller_identity for r in rows] == ["caller-2", "caller-1"]
