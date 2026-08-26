"""SkillSet control-plane errors get their HTTP status at the app layer.

The legacy ``/api/skillsets`` routes used to translate these themselves, in a
router-level ``_legacy_error`` helper that returned a hand-built
``HTTPException``. That put the status decision inside the route, and — because
the helper returned a *new* exception — the raised one and its ``__cause__``
chain never reached a log.

The mapping now lives in ``adapters.http.app._DOMAIN_ERROR_STATUS_MAP`` like
every other domain error, so this test asserts the wire from the same handler
wiring production uses.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.core.errors import DomainError
from agentclaw.community.core.skill_center.errors import (
    McpPermissionDeniedError,
    SkillSetAccessDeniedError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneLockUnavailableError,
    SkillSetControlPlaneNotFoundError,
    SkillSetRuntimeReconcileError,
)

_ROUTE_MAP: dict[str, type[DomainError]] = {
    "mcp-denied": McpPermissionDeniedError,
    "notfound": SkillSetControlPlaneNotFoundError,
    "denied": SkillSetAccessDeniedError,
    "conflict": SkillSetControlPlaneConflictError,
    "lock": SkillSetControlPlaneLockUnavailableError,
    "reconcile": SkillSetRuntimeReconcileError,
}


def _build_app() -> FastAPI:
    # Import the handlers from app.py so the test cannot drift from prod.
    from agentclaw.community.adapters.http.app import (
        _domain_error_handler,
        _unhandled_exception_handler,
    )

    app = FastAPI()
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/api/skillsets/raise/{which}")
    async def raiser(which: str):
        raise _ROUTE_MAP[which]()

    @app.get("/api/skillsets/raise-detail/{which}")
    async def raiser_with_detail(which: str):
        raise _ROUTE_MAP[which]("spelled-out reason")

    @app.get("/api/skillsets/raise-chained")
    async def raise_chained():
        try:
            raise RuntimeError("lock backend refused the connection")
        except RuntimeError as exc:
            raise SkillSetRuntimeReconcileError() from exc

    @app.get("/api/skillsets/raise-unexpected")
    async def raise_unexpected():
        raise RuntimeError("database unavailable")

    @app.get("/api/skillsets/raise-lock-chained")
    async def raise_lock_chained():
        # The shape SkillSetControlPlaneService produces: the guard's error
        # wraps the cache failure, and the control plane wraps the guard's.
        try:
            try:
                raise RuntimeError("cache lock backend unreachable")
            except RuntimeError as cache_exc:
                raise RuntimeError("mutation fence unavailable") from cache_exc
        except RuntimeError as guard_exc:
            raise SkillSetControlPlaneLockUnavailableError() from guard_exc

    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    # raise_server_exceptions=False so the catch-all handler actually runs
    # instead of TestClient re-raising before it can answer.
    return TestClient(_build_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(
    "which,expected_status,expected_detail",
    [
        ("mcp-denied", 403, "MCP permission denied"),
        ("notfound", 404, "Skill set not found"),
        ("denied", 403, "Forbidden"),
        ("conflict", 400, "Skill set operation conflict"),
        ("lock", 409, "Skill set mutation unavailable"),
        ("reconcile", 500, "Skill set runtime sync failed"),
    ],
)
def test_each_control_plane_error_maps_to_its_status(
    client: TestClient, which: str, expected_status: int, expected_detail: str
) -> None:
    response = client.get(f"/api/skillsets/raise/{which}")

    assert response.status_code == expected_status
    # The legacy surface keeps the ``{"detail": ...}`` shape its clients parse.
    assert response.json() == {"detail": expected_detail}


def test_lock_unavailable_is_a_conflict_not_a_service_outage(
    client: TestClient,
) -> None:
    """503 would be a lie, and clients act on it.

    The mutation fence is per-Bot: failing to take it means this command
    conflicts with another one in flight, while the service itself is up and
    answering. A 503 tells proxies and retry layers the whole endpoint is out
    of rotation, which is both wrong and disruptive to unrelated callers.
    """
    response = client.get("/api/skillsets/raise/lock")

    assert response.status_code == 409
    assert response.status_code != 503


def test_a_raise_site_may_say_more_than_the_default_detail(
    client: TestClient,
) -> None:
    """``remove_skill_from_set`` relies on this to keep its own 404 wording."""
    response = client.get("/api/skillsets/raise-detail/notfound")

    assert response.status_code == 404
    assert response.json() == {"detail": "spelled-out reason"}


def test_server_side_failure_logs_the_root_cause(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The reason the router no longer builds the HTTPException itself.

    A helper that returns a fresh exception discards the one that was raised,
    so the ``__cause__`` explaining *why* reconciliation failed never reached
    the log. Raising through to the app handler keeps the chain intact.
    """
    with caplog.at_level(logging.ERROR):
        response = client.get("/api/skillsets/raise-chained")

    assert response.status_code == 500
    assert "lock backend refused the connection" in caplog.text


def test_unexpected_failure_is_not_rewritten_as_a_skillset_error(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A bug is not a SkillSet outcome.

    The old helper answered every unrecognised exception with a 500 reading
    "Skill set operation failed", which named the endpoint rather than the
    fault. The catch-all reports it as what it is and logs the traceback.
    """
    with caplog.at_level(logging.ERROR):
        response = client.get("/api/skillsets/raise-unexpected")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert "database unavailable" in caplog.text


def test_status_map_covers_every_control_plane_error() -> None:
    from agentclaw.community.adapters.http.app import _DOMAIN_ERROR_STATUS_MAP

    missing = sorted(
        cls.__name__
        for cls in _ROUTE_MAP.values()
        if cls not in _DOMAIN_ERROR_STATUS_MAP
    )
    assert missing == []


def test_lock_unavailable_logs_why_the_fence_was_unavailable(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """409 is right for the caller and wrong as a traceback rule.

    Production logged exactly one line for this — "SkillSetControlPlaneLockUnavailableError
    on POST /api/skillsets/{id}/skills: Skill set mutation unavailable" — and
    nothing about the cache failure underneath, because the handler emitted a
    traceback only for 5xx and this error had just moved to 409. Both links of
    the chain must reach the log.
    """
    with caplog.at_level(logging.WARNING):
        response = client.get("/api/skillsets/raise-lock-chained")

    assert response.status_code == 409
    assert "mutation fence unavailable" in caplog.text
    assert "cache lock backend unreachable" in caplog.text
