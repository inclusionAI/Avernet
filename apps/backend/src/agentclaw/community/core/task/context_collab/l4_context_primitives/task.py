"""L4 work item authority, independent of Collet, Method and query views."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)


class TaskStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Task:
    """Identified recursive work item, not an A2A execution attempt.

    Empty completion_conditions means they have not been specified yet; the
    selected workflow still enforces its completion guards. parent_task_ref is
    absent for a root. Legacy import provenance is explicit; legacy versions
    start at the import baseline, not at a fabricated historic Task revision.
    """

    task_id: str
    collet_ref: str
    objective: str
    status: str
    version: int
    principal_ref: PrincipalRef
    executor_ref: AgentRef | None
    parent_task_ref: str | None
    completion_conditions: tuple[str, ...]
    created_event_ref: str | None
    last_event_ref: str | None
    reported_percent: int | None = None
    progress_event_ref: str | None = None
    legacy_context_version: int | None = None

    def __post_init__(self) -> None:
        for key in ("task_id", "collet_ref", "objective"):
            value = getattr(self, key)
            if not isinstance(value, str) or not value.strip():
                raise ContextCollabValidationError(f"Task.{key} must be non-empty")
        if not isinstance(self.principal_ref, PrincipalRef) or (
            self.executor_ref is not None
            and not isinstance(self.executor_ref, AgentRef)
        ):
            raise ContextCollabValidationError(
                "Task requires typed principal/executor references"
            )
        if not isinstance(self.status, str) or self.status not in {
            item.value for item in TaskStatus
        }:
            raise ContextCollabValidationError(f"unknown Task status: {self.status!r}")
        if type(self.version) is not int or self.version < 1:
            raise ContextCollabValidationError("Task.version must be positive")
        for key in (
            "parent_task_ref",
            "created_event_ref",
            "last_event_ref",
            "progress_event_ref",
        ):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ContextCollabValidationError(
                    f"Task.{key} must be non-empty or null"
                )
        if self.parent_task_ref == self.task_id:
            raise ContextCollabValidationError("Task cannot be its own parent")
        if not isinstance(self.completion_conditions, tuple) or any(
            not isinstance(value, str) or not value.strip()
            for value in self.completion_conditions
        ):
            raise ContextCollabValidationError(
                "Task completion conditions must be strings"
            )
        if self.reported_percent is not None and (
            type(self.reported_percent) is not int
            or not 0 <= self.reported_percent <= 100
        ):
            raise ContextCollabValidationError(
                "Task percent must be an integer in [0, 100]"
            )
        if (self.reported_percent is None) != (self.progress_event_ref is None):
            raise ContextCollabValidationError(
                "Task progress requires value and evidence together"
            )
        if self.legacy_context_version is None:
            if self.created_event_ref is None or self.last_event_ref is None:
                raise ContextCollabValidationError(
                    "Task requires creation and update evidence"
                )
        elif (
            type(self.legacy_context_version) is not int
            or self.legacy_context_version < 1
        ):
            raise ContextCollabValidationError(
                "legacy context version must be positive"
            )
        if self.version > 1 and self.last_event_ref is None:
            raise ContextCollabValidationError("updated Task requires update evidence")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["completion_conditions"] = list(self.completion_conditions)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        if not isinstance(data, dict):
            raise ContextCollabValidationError("Task must be an object")
        try:
            values = dict(data)
            values["principal_ref"] = PrincipalRef.from_dict(values["principal_ref"])
            executor = values["executor_ref"]
            values["executor_ref"] = (
                AgentRef.from_dict(executor) if executor is not None else None
            )
            conditions = values["completion_conditions"]
            if not isinstance(conditions, list):
                raise ContextCollabValidationError(
                    "Task completion_conditions must be a list"
                )
            values["completion_conditions"] = tuple(conditions)
            return cls(**values)
        except (KeyError, TypeError) as error:
            raise ContextCollabValidationError("invalid Task record") from error


@dataclass(frozen=True)
class TaskUpdate:
    """Already-authorized Work intent; repository fences its exact Task revision.

    G4 owns transition legality. Applying an accepted Work event increments the
    Task revision even if its lifecycle state is unchanged (e.g. progress).
    """

    task_ref: str
    expected_version: int
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.task_ref, str) or not self.task_ref.strip():
            raise ContextCollabValidationError("TaskUpdate requires a Task reference")
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ContextCollabValidationError(
                "TaskUpdate requires a positive revision"
            )
        if not isinstance(self.status, str) or self.status not in {
            item.value for item in TaskStatus
        }:
            raise ContextCollabValidationError(
                "TaskUpdate requires a valid Task status"
            )

    def apply(
        self, task: Task, *, event_id: str, event_kind: str, payload: dict
    ) -> Task:
        if self.task_ref != task.task_id or self.expected_version != task.version:
            raise ContextCollabVersionConflict("Task revision or identity changed")
        changes = {}
        if event_kind == "task.progress.updated" and "percent" in payload:
            changes.update(
                reported_percent=payload["percent"], progress_event_ref=event_id
            )
        return replace(
            task,
            status=self.status,
            version=task.version + 1,
            last_event_ref=event_id,
            **changes,
        )
