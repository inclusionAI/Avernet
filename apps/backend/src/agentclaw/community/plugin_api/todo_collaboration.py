"""Plugin API through which Todo consumes B's collaboration Context runtime.

Todo owns the product record and Human acceptance. B owns Context execution,
its authoritative runtime status, and the append-only event stream. The
contract below is transport-neutral; transport-specific adaptation belongs to
concrete implementations. The production composition currently uses the
in-process ContextCollab Service API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class TodoCollaborationError(RuntimeError):
    """B rejected a request, was unavailable, or returned a malformed payload."""


@dataclass(frozen=True)
class TodoCollaborationContextRequest:
    """Context specification created together with a new Todo."""

    space_id: str
    environment: str
    principal_id: str
    product_kind: str
    workflow_kind: str
    method_kind: str
    executor_id: str
    executor_owner_id: str
    objective: str
    todo_id: str


@dataclass(frozen=True)
class TodoCollaborationArtifact:
    """An executable result carried by a collaboration event."""

    artifact_id: str
    name: str
    url: str


@dataclass(frozen=True)
class TodoCollaborationEvent:
    """Complete normalized event projected by B for one Context."""

    event_id: str
    event_seq: int
    event_type: str
    actor_id: str
    actor_type: str
    content: str
    execution_id: str
    context_version: int | None
    status: str | None
    artifacts: tuple[TodoCollaborationArtifact, ...]
    created_at: str


@dataclass(frozen=True)
class TodoCollaborationContext:
    """Current B Context snapshot and its complete visible event stream."""

    collab_context_id: str
    status: str
    context_version: int
    current_event_id: str | None
    events: tuple[TodoCollaborationEvent, ...] = ()


@dataclass(frozen=True)
class TodoCollaborationEventRequest:
    """One event append against an expected B Context version."""

    expected_version: int
    event_kind: str
    primitive_type: str
    actor_id: str
    actor_type: str
    parent_event: str | None
    visibility: str
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TodoCollaborationPlugin(Plugin, Protocol):
    """B-side collaboration boundary used by Backend's Todo service."""

    def create_context(
        self, request: TodoCollaborationContextRequest
    ) -> TodoCollaborationContext:
        """Create, but do not run, the Context bound to a new Todo."""
        ...

    def get_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        """Read the current Context status and complete visible event stream."""
        ...

    async def run_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        """Start the configured executor and return the refreshed Context."""
        ...

    async def refresh_context_run(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        """Refresh real executor state and return the current Context projection."""
        ...

    async def resume_context(
        self,
        *,
        collab_context_id: str,
        actor_id: str,
        triggered_by_event_id: str,
    ) -> TodoCollaborationContext:
        """Continue the prior executor session from a Human interaction event."""
        ...

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        """Append an event and return its complete projection from B."""
        ...
