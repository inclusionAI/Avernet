"""Unit tests for ``TaskContextService`` — the external facade that relays the 3
trajectory operations to the internal ``TaskTrajectoryService`` (spec 2026-09-18).

Pins the relay contract (Rule 1 / Rule 3 — the public Service API means what it says):
  * ``get_trajectory`` forwards ``task_id`` + ``do_analysis`` verbatim and returns the
    inner service's result; 503/504 errors (``TrajectoryAnalysisNotConfiguredError``
    / ``TrajectoryAnalysisError``) propagate unmodified (the facade does NOT swallow
    — on-demand analysis-trigger failures must stay visible; decision #14).
  * ``emit_trajectory_event`` / ``emit_submit_trajectory`` forward every arg to the
    inner service (no leading ``repo`` arg — the repo is internal to the trajectory
    sub-module). The facade holds NO repo — it depends only on
    ``TaskTrajectoryServiceProtocol``.
  * ``TaskContextService`` structurally satisfies ``TaskContextServiceProtocol``
    (``@runtime_checkable``), so the DI binding + adapter ``Injected(...)`` resolve.
"""
from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.task.domain.errors import (
    TrajectoryAnalysisError,
    TrajectoryAnalysisNotConfiguredError,
)
from agentclaw.community.core.task.task_context.task_context_service import (
    TaskContextService,
    TaskContextServiceProtocol,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TaskTrajectory,
    TrajectoryActionType,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _trajectory() -> TaskTrajectory:
    return TaskTrajectory(
        task_id="t1", gmt_create=0, gmt_modified=0, timeline=[], analysis=None
    )


class _FakeTrajectoryService:
    """Captures calls to a ``TaskTrajectoryServiceProtocol``; scripts the
    ``get_trajectory`` response/error + records ``emit_*`` calls verbatim."""

    def __init__(self, *, trajectory=None, raise_exc=None) -> None:
        self._trajectory = trajectory if trajectory is not None else _trajectory()
        self._raise_exc = raise_exc
        self.get_calls: list[tuple[str, bool]] = []
        self.emit_event_calls: list[dict] = []
        self.emit_submit_calls: list[dict] = []

    async def get_trajectory(self, task_id, *, do_analysis=False):
        self.get_calls.append((task_id, do_analysis))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._trajectory

    def emit_trajectory_event(
        self, task_id, node_id, action_type, *, action_result,
        action_input=None, error_type=None, error_msg=None, ext_info=None,
        status_from=None, status_to=None, attempt=0, now_ms=None,
    ):
        self.emit_event_calls.append(
            {
                "task_id": task_id, "node_id": node_id, "action_type": action_type,
                "action_result": action_result, "action_input": action_input,
                "error_type": error_type, "error_msg": error_msg, "ext_info": ext_info,
                "status_from": status_from, "status_to": status_to,
                "attempt": attempt, "now_ms": now_ms,
            }
        )

    def emit_submit_trajectory(self, task_id, task_info, *, submitted_at_ms, node_id=None):
        self.emit_submit_calls.append(
            {
                "task_id": task_id, "task_info": task_info,
                "submitted_at_ms": submitted_at_ms, "node_id": node_id,
            }
        )


# ---------------------------------------------------------------------------
# get_trajectory — relay read + analysis
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_trajectory_read_relays_args_and_returns_inner_result():
    inner = _FakeTrajectoryService(trajectory=_trajectory())
    svc = TaskContextService(trajectory_service=inner)
    result = _run(svc.get_trajectory("t1", do_analysis=False))
    assert inner.get_calls == [("t1", False)]
    assert result is inner._trajectory


@pytest.mark.unit
def test_get_trajectory_analysis_relays_do_analysis_true():
    inner = _FakeTrajectoryService()
    svc = TaskContextService(trajectory_service=inner)
    _run(svc.get_trajectory("t9", do_analysis=True))
    assert inner.get_calls == [("t9", True)]


@pytest.mark.unit
def test_get_trajectory_not_configured_error_propagates_without_swallow():
    """503 path: the facade relays + does NOT swallow analysis errors."""
    inner = _FakeTrajectoryService(
        raise_exc=TrajectoryAnalysisNotConfiguredError("bot_id not configured")
    )
    svc = TaskContextService(trajectory_service=inner)
    with pytest.raises(TrajectoryAnalysisNotConfiguredError):
        _run(svc.get_trajectory("t1", do_analysis=True))
    assert inner.get_calls == [("t1", True)]


@pytest.mark.unit
def test_get_trajectory_analysis_error_propagates_without_swallow():
    """504 path: the facade relays + does NOT swallow analysis errors."""
    inner = _FakeTrajectoryService(
        raise_exc=TrajectoryAnalysisError("bot call timeout after 180s")
    )
    svc = TaskContextService(trajectory_service=inner)
    with pytest.raises(TrajectoryAnalysisError):
        _run(svc.get_trajectory("t1", do_analysis=True))


# ---------------------------------------------------------------------------
# emit_trajectory_event / emit_submit_trajectory — relay write (fire-and-forget)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_emit_trajectory_event_relays_all_kwargs_to_inner():
    inner = _FakeTrajectoryService()
    svc = TaskContextService(trajectory_service=inner)
    svc.emit_trajectory_event(
        "t1", "n1", TrajectoryActionType.EXECUTE,
        action_result="ok", action_input="req", status_from="RUNNING",
        status_to="DONE", attempt=1, ext_info={"k": "v"},
    )
    assert inner.emit_event_calls == [
        {
            "task_id": "t1", "node_id": "n1", "action_type": TrajectoryActionType.EXECUTE,
            "action_result": "ok", "action_input": "req", "error_type": None,
            "error_msg": None, "ext_info": {"k": "v"}, "status_from": "RUNNING",
            "status_to": "DONE", "attempt": 1, "now_ms": None,
        }
    ]


@pytest.mark.unit
def test_emit_trajectory_event_minimal_args_relays_defaults():
    inner = _FakeTrajectoryService()
    svc = TaskContextService(trajectory_service=inner)
    svc.emit_trajectory_event("t1", "n1", "plan", action_result="success")
    rec = inner.emit_event_calls[0]
    assert rec["task_id"] == "t1" and rec["node_id"] == "n1" and rec["action_type"] == "plan"
    assert rec["action_result"] == "success"
    assert rec["action_input"] is None and rec["error_type"] is None
    assert rec["attempt"] == 0 and rec["now_ms"] is None


@pytest.mark.unit
def test_emit_submit_trajectory_relays_to_inner():
    inner = _FakeTrajectoryService()
    svc = TaskContextService(trajectory_service=inner)
    task_info = object()  # opaque — the relay passes it through untouched
    svc.emit_submit_trajectory("t1", task_info, submitted_at_ms=123, node_id="root")
    assert inner.emit_submit_calls == [
        {"task_id": "t1", "task_info": task_info, "submitted_at_ms": 123, "node_id": "root"}
    ]


# ---------------------------------------------------------------------------
# Conformance — the impl is structurally the protocol (DI / Injected resolve)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_task_context_service_satisfies_protocol():
    """The concrete ``TaskContextService`` structurally satisfies
    ``TaskContextServiceProtocol`` (``@runtime_checkable``) — the DI binding +
    adapter ``Injected(...)`` resolve against the protocol, not the impl."""
    svc = TaskContextService(trajectory_service=_FakeTrajectoryService())
    assert isinstance(svc, TaskContextServiceProtocol)
