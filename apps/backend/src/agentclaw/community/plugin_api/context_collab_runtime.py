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


@dataclass(frozen=True)
class ContextCollabRunResult:
    """External runtime handles recorded on the subsequent task.started event."""

    run_id: str
    session_id: str | None


@runtime_checkable
class ContextCollabRuntimePlugin(Plugin, Protocol):
    """External runtime boundary consumed by ``ContextCollabService``."""

    async def run(
        self, request: ContextCollabRunRequest
    ) -> ContextCollabRunResult:
        """Start the bound executor without changing Context state directly."""
        ...
