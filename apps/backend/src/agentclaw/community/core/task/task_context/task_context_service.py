"""``TaskContextService`` — the single对外入口 for task trajectory read + write
(the facade that relays to the internal ``TaskTrajectoryService``).

Refactor (spec 2026-09-18): ``task_trajectory`` is now a sub-module of ``task_context``.
All external trajectory data access — the 2 ``GET /tasks/{id}/trajectory`` routers
(``adapters/http/task/router.py`` + ``adapters/http/openapi_v1/task/router.py``) and
the engine / task_service / callback_adapter emission gates — goes through this
service, which relays to ``TaskTrajectoryService``. The trajectory impl + repo +
``payloads.emit_*`` helpers stay INTERNAL to ``core/task/task_context/task_trajectory/``;
external callers never import the sub-module or hold the trajectory repo — they hold
``TaskContextServiceProtocol``.

Repo pattern (api/README "Where a Protocol is defined"): this Protocol is defined in
its owning core module (here), inherited by the concrete service, and re-exported by
``api/task/task_context_service.py``; adapters Inject it from ``api/``. The concrete
``TaskContextService`` lives in ``core/`` alongside the Protocol (a core→core import,
no cross-layer waiver).

Surface — one method per external trajectory operation:

* ``get_trajectory(task_id, *, do_analysis=False)`` (async) — read / on-demand bot
  analysis. Relays to the inner ``TaskTrajectoryService``; 503 (bot unconfigured) /
  504 (bot failure, no backfill) error semantics propagate unchanged.
* ``emit_trajectory_event(...)`` (sync) — fire-and-forget event write (decision #14).
  Relays; never raises; no-op when the trajectory repo is unbound (lightweight DI).

Pure relay/facade — holds NO repo, only ``TaskTrajectoryServiceProtocol``.

Optional-capability / ``None`` semantics (decision #14): emit-site consumers inject
``TaskContextServiceProtocol | None = None``; ``None`` (trajectory unbound in a
lightweight DI injector) → the gate skips emission. This mirrors the pre-refactor
``trajectory_repo | None`` no-op the engine/callback_adapter relied on. The 2 routers
Inject the non-optional Protocol (the binding is present in router injectors).

Authoritative: ``src/backend/specs/2026-09-18-task-trajectory-task-context-submodule/
spec.md``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from injector import inject

from agentclaw.community.core.task.task_context.task_trajectory.models import TaskTrajectory
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryServiceProtocol,
)

if TYPE_CHECKING:
    # The emit_* signatures reference these domain/trajectory types; the relay passes
    # values straight through to the trajectory service emitter (which reads fields via
    # attribute access at runtime). TYPE_CHECKING-guarded to keep the runtime import
    # surface minimal — mirrors payloads.py / trajectory_service.py.
    from agentclaw.community.core.task.domain.models import Status, TaskNode
    from agentclaw.community.core.task.task_context.task_trajectory.models import (
        ReasonCatalog,
        TrajectoryActionType,
    )


def build_task_runner_execution_event_kwargs(
    node: "TaskNode", mode: str, action_result: str, *, exception: Exception | None = None,
    phase: str = "dispatch", details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build task-runner EXECUTE fields for ``emit_trajectory_event``."""
    ext_info = {
        "execution_mode": mode, "assignee": node.run_info.assignee, "phase": phase,
    }
    error_type = None
    if exception is not None:
        exception_type = type(exception).__name__
        ext_info["exception_type"] = exception_type
        if isinstance(exception, (TimeoutError, ConnectionError)):
            error_type = "transport_error"
        elif exception_type in {"OpenApiAuthError", "OpenApiBadRequestError"}:
            error_type = "underlying_interface_error"
        else:
            error_type = "unclassified"
    if details:
        ext_info.update(details)
    return {
        "action_result": action_result,
        "error_type": error_type,
        "error_msg": str(exception)[:2000] if exception is not None else None,
        "ext_info": ext_info,
        "status_from": node.status,
        "status_to": node.status,
        "attempt": node.run_info.extend_props.get("harness_retries", 0),
    }


@runtime_checkable
class TaskContextServiceProtocol(Protocol):
    """The single对外 entry for trajectory read + write (relays to
    ``TaskTrajectoryService``).

    Consumers:
      * 2 HTTP routers → ``get_trajectory``.
      * ``engine.py`` / ``task_service.py`` / ``callback_adapter.py`` →
        ``emit_trajectory_event`` (fire-and-forget).
    """

    async def get_trajectory(
        self,
        task_id: str,
        *,
        do_analysis: bool = False,
        force_analysis: bool = False,
    ) -> TaskTrajectory:
        """Read the trajectory for ``task_id``; optionally trigger bot analysis
        (503/504 propagate from the inner service unchanged; ``force_analysis``
        强制重跑,跳过 timeline 版本幂等)."""
        ...

    def emit_trajectory_event(
        self,
        task_id: str,
        node_id: str,
        action_type: "TrajectoryActionType | str",
        *,
        action_result: str,
        action_input: str | None = None,
        error_type: "ReasonCatalog | str | None" = None,
        error_msg: str | None = None,
        ext_info: "dict[str, Any] | None" = None,
        status_from: "Status | str | None" = None,
        status_to: "Status | str | None" = None,
        attempt: int = 0,
        boost_reason: str | None = None,
        now_ms: int | None = None,
    ) -> None:
        """Fire-and-forget trajectory event write (never raises; decision #14)."""
        ...


class TaskContextService(TaskContextServiceProtocol):
    """Facade: relays external trajectory operations to the internal
    ``TaskTrajectoryService``. Holds no repo (pure relay); the trajectory repo is
    internal to the ``task_trajectory`` sub-module.

    ``@inject`` lets unit tests pass a fake ``TaskTrajectoryServiceProtocol`` directly
    and DI wire the real one (the composition root binds
    ``TaskTrajectoryServiceProtocol -> TaskTrajectoryService`` and
    ``TaskContextServiceProtocol -> TaskContextService``).
    """

    @inject
    def __init__(self, trajectory_service: TaskTrajectoryServiceProtocol) -> None:
        self._ts = trajectory_service

    async def get_trajectory(
        self,
        task_id: str,
        *,
        do_analysis: bool = False,
        force_analysis: bool = False,
    ) -> TaskTrajectory:
        return await self._ts.get_trajectory(
            task_id, do_analysis=do_analysis, force_analysis=force_analysis,
        )

    def emit_trajectory_event(
        self,
        task_id: str,
        node_id: str,
        action_type: "TrajectoryActionType | str",
        *,
        action_result: str,
        action_input: str | None = None,
        error_type: "ReasonCatalog | str | None" = None,
        error_msg: str | None = None,
        ext_info: "dict[str, Any] | None" = None,
        status_from: "Status | str | None" = None,
        status_to: "Status | str | None" = None,
        attempt: int = 0,
        boost_reason: str | None = None,
        now_ms: int | None = None,
    ) -> None:
        self._ts.emit_trajectory_event(
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
