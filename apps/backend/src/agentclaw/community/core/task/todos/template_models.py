"""Transport-neutral definitions for versioned Todo product templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar


class _DocumentedTemplateEnum(StrEnum):
    """Base for published Todo template enum values."""

    __descriptions__: ClassVar[dict[str, str]] = {}

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Any:
        schema = handler(core_schema)
        schema["description"] = (cls.__doc__ or "").strip()
        values = list(schema.get("enum", []))
        schema["x-enum-descriptions"] = [
            cls.__descriptions__.get(value, "") for value in values
        ]
        names = {member.value: member.name for member in cls}
        schema["x-enum-varnames"] = [names.get(value, str(value)) for value in values]
        schema["x-enumNames"] = list(schema["x-enum-varnames"])
        return schema


class TodoParticipantSubjectType(_DocumentedTemplateEnum):
    """Identity type accepted by a Todo participant role binding."""

    BOT = "bot"
    HUMAN = "human"

    __descriptions__ = {
        "bot": "A Bot identity installed in the current environment.",
        "human": "A user identity available to the Todo workbench.",
    }


class TodoParticipantRefSource(_DocumentedTemplateEnum):
    """Resolution rule for a stage executor or owner identity."""

    BINDING = "binding"
    FIXED = "fixed"
    TODO_CREATOR = "todo_creator"

    __descriptions__ = {
        "binding": "Resolve from a participant role bound at Todo creation.",
        "fixed": "Use the fixed identity embedded in the template stage.",
        "todo_creator": "Use the user identity that created the Todo.",
    }


class TodoParticipantRequiredAt(_DocumentedTemplateEnum):
    """Lifecycle point at which a participant role must be bound."""

    TODO_CREATE = "todo_create"

    __descriptions__ = {
        "todo_create": "The role must be bound when the Todo is created.",
    }


class TodoStageKind(_DocumentedTemplateEnum):
    """Execution kind of one ordered Todo template stage."""

    BOT = "bot"
    HUMAN = "human"

    __descriptions__ = {
        "bot": "The stage advances when its bound Bot execution completes through the /complete contract; blocked is an internal lifecycle projection.",
        "human": "The stage waits for its bound Human executor to complete it.",
    }


@dataclass(frozen=True)
class TodoParticipantDefinition:
    """One participant role that must be bound when a Todo is created."""

    key: str
    label: str
    subject_type: TodoParticipantSubjectType
    required_at: TodoParticipantRequiredAt


@dataclass(frozen=True)
class TodoParticipantReference:
    """Tagged identity reference used by a stage executor or owner."""

    source: TodoParticipantRefSource
    key: str | None = None
    subject_type: TodoParticipantSubjectType | None = None
    subject_id: str | None = None


@dataclass(frozen=True)
class TodoRequiredArtifactGate:
    """Deterministic artifact fact required before a stage may complete."""

    artifact_type: str
    artifact_state: str
    min_count: int = 1


@dataclass(frozen=True)
class TodoCompletionGate:
    """Optional deterministic completion requirements for one stage."""

    required_artifacts: tuple[TodoRequiredArtifactGate, ...] = field(
        default_factory=tuple
    )


@dataclass(frozen=True)
class TodoStageDefinition:
    """One ordered, user-visible Bot or Human product stage."""

    key: str
    name: str
    purpose: str
    stage_kind: TodoStageKind
    executor_ref: TodoParticipantReference
    owner_ref: TodoParticipantReference | None = None
    completion_gate: TodoCompletionGate = field(default_factory=TodoCompletionGate)


@dataclass(frozen=True)
class TodoTemplateDefinition:
    """Complete immutable definition persisted by a template version."""

    schema_version: str
    name: str
    description: str
    binding_schema: tuple[TodoParticipantDefinition, ...]
    stages: tuple[TodoStageDefinition, ...]
