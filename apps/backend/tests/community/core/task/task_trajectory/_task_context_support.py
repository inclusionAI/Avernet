"""Test support for the spec-2026-09-18 trajectory → ``task_context_service`` reroute.

After the reroute, ``CentralizedExecutionAdapter`` / ``TaskService`` / ``TaskLoopCallback`` take
``task_context_service: TaskContextServiceProtocol | None`` instead of a raw
``TaskTrajectoryRepositoryProtocol`` (the trajectory repo is now internal to the
``task_context.task_trajectory`` sub-module, reached only via the facade). The gate
/ e2e tests, however, are written around passing a trajectory *repo* (a
``TrajectoryEventRecord`` fake) and asserting on its records. ``_tcs(repo)`` bridges
the two: it wraps a repo as a ``TaskContextServiceProtocol`` whose ``emit_*`` relay
to the SAME ``payloads.emit_*`` free funcs the real
``TaskContextService → TaskTrajectoryService`` chain uses — so existing repo-record
assertions hold unchanged.

``_tcs(None)`` returns ``None`` (the "trajectory unbound" case): the engine's /
callback's ``if task_context_service is None`` guard then skips emission, mirroring
production where the DI try/except yields ``None``. ``get_trajectory`` is unused by
the emission gate tests and raises on accidental use (read-path coverage lives in
``test_trajectory_service.py`` + ``test_task_context_service.py``).
"""
from __future__ import annotations

from agentclaw.community.core.task.task_context.task_trajectory.payloads import (
    emit_trajectory_event as _emit_trajectory_event,
)


class _TestTaskContextService:
    """Minimal ``TaskContextServiceProtocol`` for emission-gate tests: relays
    ``emit_*`` straight to the ``payloads`` free funcs with the wrapped repo."""

    def __init__(self, repo) -> None:
        self._repo = repo

    async def get_trajectory(self, task_id, *, do_analysis=False):
        # Gate/e2e emission tests never read trajectory; raise on accidental use.
        raise AssertionError("_tcs gates/e2e never call get_trajectory")

    def emit_trajectory_event(
        self,
        task_id,
        node_id,
        action_type,
        *,
        action_result,
        action_input=None,
        error_type=None,
        error_msg=None,
        ext_info=None,
        status_from=None,
        status_to=None,
        attempt=0,
        boost_reason=None,
        now_ms=None,
    ):
        _emit_trajectory_event(
            self._repo,
            task_id,
            node_id,
            action_type,
            action_result=action_result,
            action_input=action_input,
            error_type=error_type,
            error_msg=error_msg,
            ext_info=ext_info,
            status_from=status_from,
            status_to=status_to,
            attempt=attempt,
            boost_reason=boost_reason,
            now_ms=now_ms,
        )



def _tcs(repo):
    """Wrap a trajectory repo as a ``task_context_service`` for gate/e2e tests.

    ``repo is None`` → ``None`` (the unbound / no-op case, exercising the engine +
    callback ``is None`` guard). Otherwise a ``_TestTaskContextService`` relaying
    ``emit_*`` to the repo via the ``payloads`` free funcs.
    """
    return None if repo is None else _TestTaskContextService(repo)
