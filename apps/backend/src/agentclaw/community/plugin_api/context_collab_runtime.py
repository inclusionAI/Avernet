"""Runtime Plugin API for executing one collaboration Context.

The Context collaboration core owns lifecycle policy and event transitions. The
runtime plugin owns the external Bot invocation only; it does not mutate Context
state or persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class ContextCollabRuntimeError(RuntimeError):
    """The configured Context runtime could not start the executor."""


@dataclass(frozen=True)
class ContextCollabRunRequest:
    """Transport-neutral input for starting the executor bound to a Context."""

    collab_context_id: str
    executor_id: str
    principal_id: str
    objective: str
    todo_id: str
    executor_owner_id: str | None = None
    todo_execution_id: str | None = None
    todo_execution_token: str | None = None


@dataclass(frozen=True)
class ContextCollabResumeRequest:
    """Transport-neutral input for continuing the previous executor session."""

    collab_context_id: str
    executor_id: str
    principal_id: str
    todo_id: str
    session_id: str
    message: str
    interaction_event_id: str
    executor_owner_id: str | None = None
    todo_execution_id: str | None = None
    todo_execution_token: str | None = None
    group_id: str | None = None


@dataclass(frozen=True)
class ContextCollabRunResult:
    """External runtime handles recorded on the subsequent ``task.started`` event.

    A direct Bot transport has ``run_id``. A BCS manager-worker transport has
    ``group_id`` plus ``session_id`` and may not expose a per-message run id.
    Callers must preserve the real handle kind instead of encoding a group id as
    a fake run id.
    """

    run_id: str | None
    session_id: str | None
    group_id: str | None = None


@dataclass(frozen=True)
class ContextCollabRunStatus:
    """Transport-neutral snapshot of one external executor run or group session."""

    run_id: str | None
    status: str
    result: object | None
    error: object | None
    completed_at: str | None
    session_id: str | None
    group_id: str | None = None


@runtime_checkable
class ContextCollabRuntimePlugin(Plugin, Protocol):
    """External runtime boundary consumed by ``ContextCollabService``."""

    async def run(self, request: ContextCollabRunRequest) -> ContextCollabRunResult:
        """Start the bound executor without changing Context state directly."""
        ...

    async def resume(
        self, request: ContextCollabResumeRequest
    ) -> ContextCollabRunResult:
        """Continue the previous executor session without mutating Context state."""
        ...

    async def get_run(
        self,
        run_id: str | None = None,
        *,
        group_id: str | None = None,
        session_id: str | None = None,
    ) -> ContextCollabRunStatus:
        """Read one real direct run or BCS group-session state without mutation."""
        ...
