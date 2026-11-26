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
from agentclaw.community.core.task.context_collab.l4_context_primitives.legacy_artifact import (
    LegacyArtifact,
)


def normalize_executor_refs(
    refs: tuple[AgentRef, ...] | list[AgentRef],
) -> tuple[AgentRef, ...]:
    """Validate an ordered set of typed executors; empty means unassigned."""
    if not isinstance(refs, (tuple, list)) or any(
        not isinstance(ref, AgentRef) for ref in refs
    ):
        raise ContextCollabValidationError("executor_refs must contain typed AgentRefs")
    result = tuple(refs)
    for ref in result:
        AgentRef.from_dict(ref.to_dict())
    if len(set(result)) != len(result):
        raise ContextCollabValidationError("executor_refs must not contain duplicates")
    return result


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
    artifacts contains L4 Artifact values, not resource bodies. It defaults
    to empty; Work/report handling does not populate or aggregate it yet.
    """

    task_id: str
    collet_ref: str
    objective: str
    status: str
    version: int
    principal_ref: PrincipalRef
    executor_refs: tuple[AgentRef, ...]
    parent_task_ref: str | None
    completion_conditions: tuple[str, ...]
    created_event_ref: str | None
    last_event_ref: str | None
    reported_percent: int | None = None
    progress_event_ref: str | None = None
    legacy_context_version: int | None = None
    artifacts: tuple[LegacyArtifact, ...] = ()

    def __post_init__(self) -> None:
        for key in ("task_id", "collet_ref", "objective"):
            value = getattr(self, key)
            if not isinstance(value, str) or not value.strip():
                raise ContextCollabValidationError(f"Task.{key} must be non-empty")
        if not isinstance(self.principal_ref, PrincipalRef):
            raise ContextCollabValidationError(
                "Task requires typed principal/executor references"
            )
        object.__setattr__(
            self, "executor_refs", normalize_executor_refs(self.executor_refs)
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
        if not isinstance(self.artifacts, tuple) or any(
            not isinstance(artifact, LegacyArtifact) for artifact in self.artifacts
        ):
            raise ContextCollabValidationError(
                "Task.artifacts must be a tuple of L4 Artifact values"
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

    @property
    def executor_ref(self) -> AgentRef | None:
        """Legacy single-executor view; multiple executors have no sole executor.

        Collection-aware Work/authorization must use executor_refs, not this view.
        """
        return self.executor_refs[0] if len(self.executor_refs) == 1 else None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["executor_refs"] = [ref.to_dict() for ref in self.executor_refs]
        data["completion_conditions"] = list(self.completion_conditions)
        data["artifacts"] = [artifact.to_dict() for artifact in self.artifacts]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        if not isinstance(data, dict):
            raise ContextCollabValidationError("Task must be an object")
        try:
            values = dict(data)
            if not isinstance(values["principal_ref"], dict):
                raise ContextCollabValidationError(
                    "Task principal_ref must be an object"
                )
            values["principal_ref"] = PrincipalRef.from_dict(values["principal_ref"])
            if "executor_refs" in values:
                if "executor_ref" in values or not isinstance(
                    values["executor_refs"], list
                ):
                    raise ContextCollabValidationError(
                        "ambiguous or invalid Task executors"
                    )
                if any(not isinstance(ref, dict) for ref in values["executor_refs"]):
                    raise ContextCollabValidationError(
                        "Task executor_refs must contain objects"
                    )
                values["executor_refs"] = tuple(
                    AgentRef.from_dict(ref) for ref in values["executor_refs"]
                )
            else:
                # Explicit read compatibility for the previously persisted Task JSON.
                executor = values.pop("executor_ref")
                if executor is not None and not isinstance(executor, dict):
                    raise ContextCollabValidationError(
                        "Task executor_ref must be an object or null"
                    )
                values["executor_refs"] = (
                    (AgentRef.from_dict(executor),) if executor is not None else ()
                )
            conditions = values["completion_conditions"]
            if not isinstance(conditions, list):
                raise ContextCollabValidationError(
                    "Task completion_conditions must be a list"
                )
            values["completion_conditions"] = tuple(conditions)
            # Older Task records do not declare artifacts.
            artifacts = values.get("artifacts", [])
            if not isinstance(artifacts, list) or any(
                not isinstance(artifact, dict) for artifact in artifacts
            ):
                raise ContextCollabValidationError(
                    "Task artifacts must be a list of objects"
                )
            values["artifacts"] = tuple(
                LegacyArtifact.from_dict(artifact) for artifact in artifacts
            )
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
