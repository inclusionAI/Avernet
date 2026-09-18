"""E2e tests for ``GET /tasks/{task_id}/trajectory`` (REQ-8, P5b endpoint).

Covers both the public OpenAPI mirror (``/openapi/v1/collaboration/tasks/trajectory``)
and the internal副本 (``/api/v1/collaboration/tasks/trajectory``) via the shared
``TestClient`` + ``attach_injector`` pattern (mirrors
``test_dashboard_assignee_bot_info.py``). The ``TaskContextServiceProtocol``
is stubbed at the injector level (a ``_StubModule``) so the assertions exercise
the router → service → DTO translation, not the real assembler/analyzer/bot.

Cases (spec REQ-8/REQ-10 e2e — success / interface_error / timeout /
not-configured, all via ``do_analysis=true``):
* default ``do_analysis=false`` — DTO with ``analysis=null`` for never-analyzed;
  no write (stub never receives ``do_analysis=True``).
* ``do_analysis=true`` success — DTO with ``analysis`` non-null (the JSON the
  stub returned).
* ``do_analysis=true`` bot-failure/timeout — ``TrajectoryAnalysisError`` →
  HTTP 504 + ``analysis`` unchanged (no backfill; stub raised).
* ``do_analysis=true`` bot-not-configured —
  ``TrajectoryAnalysisNotConfiguredError`` → HTTP 503.
* internal router mirror returns the same DTO shape.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.task.router import (
    router as task_router,
)
from agentclaw.community.adapters.http.task.router import (
    router as task_internal_router,
)
from agentclaw.community.api.task.task_context_service import (
    TaskContextServiceProtocol,
)
from agentclaw.community.core.task.domain.errors import (
    TrajectoryAnalysisError,
    TrajectoryAnalysisNotConfiguredError,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    AnalysisType,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryAnalysis,
    TrajectoryEvent,
)


# ---------------------------------------------------------------------------
# Stub service — configurable per-test (records calls; scripts response/error)
# ---------------------------------------------------------------------------


class _StubTrajectoryService:
    """Stub ``TaskContextServiceProtocol`` capturing ``get_trajectory`` calls."""

    def __init__(
        self,
        *,
        trajectory: TaskTrajectory | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._trajectory = trajectory
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, bool]] = []

    async def get_trajectory(
        self, task_id: str, *, do_analysis: bool = False
    ) -> TaskTrajectory:
        self.calls.append((task_id, do_analysis))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._trajectory

    # The endpoint exercises get_trajectory only; the protocol's emit_* (write
    # path) are out-of-scope here — no-op stubs so the stub structurally
    # satisfies TaskContextServiceProtocol for the injector binding.
    def emit_trajectory_event(self, *args, **kwargs) -> None:
        pass  # pragma: no cover

    def emit_submit_trajectory(self, *args, **kwargs) -> None:
        pass  # pragma: no cover


class _StubModule(Module):
    """Binds ``TaskContextServiceProtocol`` to a stub instance (injector-level
    override, mirroring ``test_dashboard_assignee_bot_info.py``'s ``_StubModule``)."""

    def __init__(self, service: _StubTrajectoryService) -> None:
        super().__init__()
        self._service = service

    @singleton
    @provider
    def trajectory_service(self) -> TaskContextServiceProtocol:
        return self._service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_trajectory(*, analysis: str | None = None) -> TaskTrajectory:
    return TaskTrajectory(
        task_id="t1",
        gmt_create=1000,
        gmt_modified=1000,
        timeline=[
            TrajectoryEvent(
                task_id="t1",
                node_id="n1",
                action_type=TrajectoryActionType.SUBMIT,
                action_result="success",
                attempt=0,
                gmt_create=1000,
                gmt_modified=1000,
            ),
            TrajectoryEvent(
                task_id="t1",
                node_id="n1",
                action_type=TrajectoryActionType.DISPATCH,
                action_result="hit_single",
                attempt=0,
                gmt_create=2000,
                gmt_modified=2000,
            ),
        ],
        analysis=analysis,
    )


def _analysis_json() -> str:
    return json.dumps(
        {
            "analysis_type": "tc_bot",
            "analysis_executor": "bot-traj-analyst",
            "analysis_input": "events=2",
            "analysis_output": "boost_reason: x",
            "gmt_create": 5000,
            "boost_reason": "策略=search 选中=botA(hit_single)",
            "failure_reason": None,
        },
        ensure_ascii=False,
    )


def _build_client(service: _StubTrajectoryService) -> TestClient:
    injector = Injector([_StubModule(service)])
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "traj-owner"}
    attach_injector(app, injector)
    return TestClient(app)


@pytest.fixture
def stub_service():
    return _StubTrajectoryService(trajectory=_make_trajectory(analysis=None))


@pytest.fixture
def client(stub_service):
    return _build_client(stub_service)


# ---------------------------------------------------------------------------
# do_analysis=false (default, pure read)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_default_read_returns_dto_with_null_analysis(client, stub_service):
    """GET /trajectory (default do_analysis=false) → 200, DTO, analysis=null."""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"}
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["task_id"] == "t1"
    assert data["analysis"] is None
    assert len(data["timeline"]) == 2
    assert data["timeline"][0]["action_type"] == "submit"
    assert data["timeline"][1]["action_type"] == "dispatch"
    # stub received do_analysis=False (default)
    assert stub_service.calls == [("t1", False)]


@pytest.mark.unit
def test_trajectory_explicit_do_analysis_false(client, stub_service):
    """?do_analysis=false → same pure-read shape."""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "false"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["analysis"] is None
    assert stub_service.calls == [("t1", False)]


@pytest.mark.unit
def test_trajectory_surfaces_persisted_analysis(client):
    """A persisted analysis (never-analyzed head still carries it) is surfaced."""
    persisted = _analysis_json()
    svc = _StubTrajectoryService(trajectory=_make_trajectory(analysis=persisted))
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["analysis"] == persisted


# ---------------------------------------------------------------------------
# do_analysis=true success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_do_analysis_true_returns_fresh_analysis():
    """?do_analysis=true success → 200, DTO.analysis = fresh JSON from service."""
    svc = _StubTrajectoryService(
        trajectory=_make_trajectory(analysis=_analysis_json())
    )
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["analysis"] is not None
    parsed = json.loads(data["analysis"])
    assert parsed["analysis_type"] == "tc_bot"
    assert parsed["analysis_executor"] == "bot-traj-analyst"
    assert svc.calls == [("t1", True)]


# ---------------------------------------------------------------------------
# do_analysis=true bot failure/timeout → 504
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_do_analysis_true_bot_failure_returns_504():
    """Bot failure → TrajectoryAnalysisError → HTTP 504 (决策 #14)."""
    svc = _StubTrajectoryService(
        trajectory=_make_trajectory(analysis=None),
        raise_exc=TrajectoryAnalysisError("bot timed out"),
    )
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 504
    assert svc.calls == [("t1", True)]


@pytest.mark.unit
def test_trajectory_do_analysis_true_bot_timeout_returns_504():
    """Bot timeout is the same TrajectoryAnalysisError → HTTP 504."""
    svc = _StubTrajectoryService(
        trajectory=_make_trajectory(analysis=None),
        raise_exc=TrajectoryAnalysisError("bot call timeout after 180s"),
    )
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 504


# ---------------------------------------------------------------------------
# do_analysis=true bot not configured → 503
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_do_analysis_true_not_configured_returns_503():
    """Bot not configured → TrajectoryAnalysisNotConfiguredError → HTTP 503."""
    svc = _StubTrajectoryService(
        trajectory=_make_trajectory(analysis=None),
        raise_exc=TrajectoryAnalysisNotConfiguredError("bot_id not configured"),
    )
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 503
    assert svc.calls == [("t1", True)]


# ---------------------------------------------------------------------------
# Internal router mirror (same DTO shape, no PrincipalDep)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_internal_trajectory_mirror_returns_same_dto_shape():
    """GET /api/v1/collaboration/tasks/trajectory (internal副本) → same DTO."""
    # internal router has no PrincipalDep; the auth override on the app is
    # harmless for the internal route which doesn't declare principal.
    svc = _StubTrajectoryService(trajectory=_make_trajectory(analysis=_analysis_json()))
    c = _build_client(svc)
    r = c.get(
        "/api/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["task_id"] == "t1"
    assert json.loads(data["analysis"])["analysis_type"] == "tc_bot"
    assert svc.calls == [("t1", True)]


@pytest.mark.unit
def test_internal_trajectory_default_read():
    """Internal副本 default read → DTO with analysis=null."""
    svc = _StubTrajectoryService(trajectory=_make_trajectory(analysis=None))
    c = _build_client(svc)
    r = c.get(
        "/api/v1/collaboration/tasks/trajectory", params={"task_id": "t1"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["analysis"] is None
    assert svc.calls == [("t1", False)]


# ---------------------------------------------------------------------------
# DTO shape — flat fields, enums as .value strings, no ext_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_dto_has_flat_event_fields_no_ext_info(client):
    """The event DTO carries the flat TrajectoryEvent fields and NO ext_info
    (REQ-1: ext_info is not on the domain object)."""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"}
    )
    assert r.status_code == 200
    ev = r.json()["data"]["timeline"][0]
    # flat fields present
    for field in (
        "task_id", "node_id", "action_type", "action_result", "attempt",
        "gmt_create", "gmt_modified", "action_input", "status_from",
        "status_to", "error_type", "error_msg", "analysis",
    ):
        assert field in ev, f"missing field {field}"
    # ext_info is NOT on the DTO (REQ-1)
    assert "ext_info" not in ev
    # action_type is the string value, not the enum object
    assert ev["action_type"] == "submit"
