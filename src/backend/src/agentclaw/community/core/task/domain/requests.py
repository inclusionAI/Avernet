"""External execute-request contract for the collaboration-task module.

Flattened, externally-facing request object (aligns with the open execute
contract). Lives in the domain layer because ``core/`` may not import ``api/``;
the service protocol (``api/``) and the HTTP DTO (``adapters/http/``) import
from here. Pure dataclass — zero transport/framework deps, per the domain rule.

``task_id`` is NOT part of the request: the service generates it (via an
injected ``task_id_provider``) and passes it into :meth:`to_task_info`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    TaskInfo,
    TaskSpec,
)


@dataclass(frozen=True)
class RequestMetadata:
    title: str
    instruction: str


@dataclass(frozen=True)
class RequestContext:
    background: str
    extend_props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestAcceptance:
    id: str
    acceptance: str


@dataclass(frozen=True)
class RequestGoal:
    objective: str
    acceptances: list[RequestAcceptance] = field(default_factory=list)


@dataclass(frozen=True)
class RequestTaskSpec:
    metadata: RequestMetadata
    context: RequestContext
    goal: RequestGoal


@dataclass(frozen=True)
class TaskInfoRequest:
    task_spec: RequestTaskSpec
    source_type: "TaskSourceType"  # noqa: F821 — defined in models.py
    owner_user_id: str
    owner_bot_id: str
    execution_config: dict[str, Any] = field(default_factory=dict)

    def to_task_info(self, task_id: str) -> TaskInfo:
        """Map the request onto the internal ``TaskInfo`` (server-supplied ``task_id``).

        ``acceptance`` → domain ``AcceptanceCriteria.description``;
        ``source_type`` = ``source_type.value``; ``owner_bot_id`` = ``owner_bot_id`` (D3).
        """
        return TaskInfo(
            task_spec=TaskSpec(
                metadata=Metadata(task_id=task_id,
                                  title=self.task_spec.metadata.title,
                                  instruction=self.task_spec.metadata.instruction),
                context=Context(background=self.task_spec.context.background,
                                extend_props=dict(self.task_spec.context.extend_props)),
                goal=Goal(objective=self.task_spec.goal.objective,
                          acceptances=[AcceptanceCriteria(id=a.id, description=a.acceptance)
                                       for a in self.task_spec.goal.acceptances]),
            ),
            source_type=self.source_type.value,
            owner_bot_id=self.owner_bot_id,
            execution_config=dict(self.execution_config),
        )
