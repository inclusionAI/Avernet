"""External collaboration-plugin contract for Todo context and events.

The B-side collaboration service owns Context assembly, execution/run state and
append-only collaboration events. Backend's Todo service is only a consumer of
this Plugin API: it creates one collaboration context per initial Todo run,
writes Human interaction/acceptance events, and projects the returned event
stream for the workbench UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class TodoCollaborationError(RuntimeError):
    """B rejected a request, was unavailable, or returned a malformed payload."""


@dataclass(frozen=True)
class TodoCollaborationContextRequest:
    """Transport-neutral input for creating a collaboration Context."""

    task_id: str
    task_title: str
    collaboration_goal: str
    collaborators: tuple[str, ...]
    owner_id: str
    bot_id: str


@dataclass(frozen=True)
class TodoCollaborationContext:
    """The stable B-side Context identity attached to one Todo."""

    collab_context_id: str


@dataclass(frozen=True)
class TodoCollaborationEventRequest:
    """A Human event sent to an existing collaboration Context.

    B owns snapshot assembly. ``boundary_event_seq`` is the last event sequence
    already seen by the Human UI; it is submitted for traceability, not stored
    by A.
    """

    event_type: str
    actor_id: str
    content: str
    boundary_event_seq: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TodoCollaborationArtifact:
    """An executable result produced by a collaboration event."""

    artifact_id: str
    name: str
    url: str


@dataclass(frozen=True)
class TodoCollaborationEvent:
    """The normalized event shape exposed by B for one collaboration Context."""

    event_id: str
    event_seq: int
    event_type: str
    actor_id: str
    actor_type: str
    content: str
    execution_id: str
    context_version: str
    status: str | None
    artifacts: tuple[TodoCollaborationArtifact, ...]
    created_at: str


@runtime_checkable
class TodoCollaborationPlugin(Plugin, Protocol):
    """B-side collaboration boundary used by Backend's Todo service."""

    def create_context(
        self, request: TodoCollaborationContextRequest
    ) -> TodoCollaborationContext:
        """Create a Context and start the initial single-Bot collaboration."""
        ...

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        """Append one Human event; B decides whether it resumes execution."""
        ...

    def list_events(
        self, *, collab_context_id: str
    ) -> tuple[TodoCollaborationEvent, ...]:
        """Return the complete B event stream for a collaboration Context."""
        ...
