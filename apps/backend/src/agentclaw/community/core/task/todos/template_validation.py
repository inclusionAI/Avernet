"""Domain validation and normalization for Todo template definitions."""

from __future__ import annotations

from dataclasses import replace

from agentclaw.community.core.task.todos.errors import TodoTemplateValidationError
from agentclaw.community.core.task.todos.template_capabilities import (
    TODO_TEMPLATE_CAPABILITY_REGISTRY,
    TodoTemplateCapabilityRegistry,
)
from agentclaw.community.core.task.todos.template_models import (
    TodoParticipantDefinition,
    TodoParticipantReference,
    TodoParticipantRefSource,
    TodoParticipantRequiredAt,
    TodoParticipantSubjectType,
    TodoStageDefinition,
    TodoStageKind,
    TodoTemplateDefinition,
)


class TodoTemplateDefinitionValidator:
    """Validate one supported linear template and return its normalized form."""

    def __init__(
        self,
        registry: TodoTemplateCapabilityRegistry = TODO_TEMPLATE_CAPABILITY_REGISTRY,
    ) -> None:
        self._registry = registry

    def validate(self, definition: TodoTemplateDefinition) -> TodoTemplateDefinition:
        if not self._registry.supports_schema_version(definition.schema_version):
            raise TodoTemplateValidationError(
                f"unsupported template schema_version {definition.schema_version!r}"
            )
        self._required_text(definition.name, "template name")
        self._text(definition.description, "template description")
        if not definition.stages:
            raise TodoTemplateValidationError("template must define at least one stage")

        participants = self._participants(definition.binding_schema)
        self._unique_stage_keys(definition.stages)
        normalized_stages = tuple(
            self._stage(stage, participants) for stage in definition.stages
        )
        return replace(definition, stages=normalized_stages)

    def _participants(
        self,
        definitions: tuple[TodoParticipantDefinition, ...],
    ) -> dict[str, TodoParticipantDefinition]:
        participants: dict[str, TodoParticipantDefinition] = {}
        for participant in definitions:
            self._required_text(participant.key, "participant key")
            self._required_text(participant.label, "participant label")
            if participant.key in participants:
                raise TodoTemplateValidationError(
                    f"duplicate participant key {participant.key!r}"
                )
            if participant.subject_type not in (
                TodoParticipantSubjectType.BOT,
                TodoParticipantSubjectType.HUMAN,
            ):
                raise TodoTemplateValidationError(
                    f"participant {participant.key!r} has unsupported subject_type"
                )
            if participant.required_at is not TodoParticipantRequiredAt.TODO_CREATE:
                raise TodoTemplateValidationError(
                    f"participant {participant.key!r} required_at must be todo_create"
                )
            participants[participant.key] = participant
        return participants

    def _unique_stage_keys(self, stages: tuple[TodoStageDefinition, ...]) -> None:
        keys: set[str] = set()
        for stage in stages:
            self._required_text(stage.key, "stage key")
            if stage.key in keys:
                raise TodoTemplateValidationError(f"duplicate stage key {stage.key!r}")
            keys.add(stage.key)

    def _stage(
        self,
        stage: TodoStageDefinition,
        participants: dict[str, TodoParticipantDefinition],
    ) -> TodoStageDefinition:
        self._required_text(stage.name, f"stage {stage.key!r} name")
        self._required_text(stage.purpose, f"stage {stage.key!r} purpose")
        if stage.stage_kind is TodoStageKind.BOT:
            executor_type = TodoParticipantSubjectType.BOT
            executor_description = "bot stage executor"
        elif stage.stage_kind is TodoStageKind.HUMAN:
            executor_type = TodoParticipantSubjectType.HUMAN
            executor_description = "human stage executor"
        else:
            raise TodoTemplateValidationError(
                f"stage {stage.key!r} has unsupported stage_kind"
            )

        actual_executor_type = self._reference_subject_type(
            stage.executor_ref,
            participants,
            role="executor",
        )
        if actual_executor_type is not executor_type:
            raise TodoTemplateValidationError(
                f"{executor_description} must resolve to {executor_type.value}"
            )

        owner_ref = stage.owner_ref
        if owner_ref is None:
            if stage.stage_kind is TodoStageKind.BOT:
                raise TodoTemplateValidationError("bot stage owner must be configured")
            owner_ref = stage.executor_ref
        owner_type = self._reference_subject_type(
            owner_ref,
            participants,
            role="owner",
        )
        if owner_type is not TodoParticipantSubjectType.HUMAN:
            raise TodoTemplateValidationError("stage owner must resolve to human")

        seen_gates: set[tuple[str, str]] = set()
        for gate in stage.completion_gate.required_artifacts:
            self._required_text(gate.artifact_type, "artifact gate type")
            self._required_text(gate.artifact_state, "artifact gate state")
            if gate.min_count < 1:
                raise TodoTemplateValidationError(
                    "artifact gate min_count must be at least 1"
                )
            gate_key = (gate.artifact_type, gate.artifact_state)
            if gate_key in seen_gates:
                raise TodoTemplateValidationError(
                    "duplicate hard artifact gate "
                    f"{gate.artifact_type}:{gate.artifact_state}"
                )
            seen_gates.add(gate_key)
            if not self._registry.supports_hard_gate(*gate_key):
                raise TodoTemplateValidationError(
                    "unsupported hard artifact gate "
                    f"{gate.artifact_type}:{gate.artifact_state}"
                )
        return replace(stage, owner_ref=owner_ref)

    def _reference_subject_type(
        self,
        reference: TodoParticipantReference,
        participants: dict[str, TodoParticipantDefinition],
        *,
        role: str,
    ) -> TodoParticipantSubjectType:
        if reference.source is TodoParticipantRefSource.BINDING:
            if (
                not reference.key
                or reference.subject_type is not None
                or reference.subject_id is not None
            ):
                raise TodoTemplateValidationError(
                    "binding reference requires only a participant key"
                )
            participant = participants.get(reference.key)
            if participant is None:
                raise TodoTemplateValidationError(
                    f"binding reference names unknown participant {reference.key!r}"
                )
            return participant.subject_type

        if reference.source is TodoParticipantRefSource.FIXED:
            if (
                reference.key is not None
                or reference.subject_type
                not in (
                    TodoParticipantSubjectType.BOT,
                    TodoParticipantSubjectType.HUMAN,
                )
                or not reference.subject_id
            ):
                raise TodoTemplateValidationError(
                    "fixed reference requires only subject_type and subject_id"
                )
            return reference.subject_type

        if reference.source is TodoParticipantRefSource.TODO_CREATOR:
            if (
                reference.key is not None
                or reference.subject_type is not None
                or reference.subject_id is not None
                or role != "owner"
            ):
                raise TodoTemplateValidationError(
                    "todo_creator reference is only valid as a human owner"
                )
            return TodoParticipantSubjectType.HUMAN

        raise TodoTemplateValidationError("unsupported participant reference source")

    @staticmethod
    def _required_text(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise TodoTemplateValidationError(f"{field_name} must not be empty")

    @staticmethod
    def _text(value: str, field_name: str) -> None:
        if not isinstance(value, str):
            raise TodoTemplateValidationError(f"{field_name} must be text")
