"""Canonical persistence codec for Todo template definitions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from agentclaw.community.core.task.todos.errors import TodoTemplateValidationError
from agentclaw.community.core.task.todos.template_models import (
    TodoCompletionGate,
    TodoParticipantDefinition,
    TodoParticipantReference,
    TodoParticipantRefSource,
    TodoParticipantRequiredAt,
    TodoParticipantSubjectType,
    TodoRequiredArtifactGate,
    TodoStageDefinition,
    TodoStageKind,
    TodoTemplateDefinition,
)
from agentclaw.community.core.task.todos.template_validation import (
    TodoTemplateDefinitionValidator,
)


@dataclass(frozen=True)
class EncodedTodoTemplateDefinition:
    """Canonical JSON and its SHA-256 digest."""

    definition_json: str
    definition_hash: str


class TodoTemplateDefinitionCodec:
    """Encode, hash, and strictly decode one supported template definition."""

    def __init__(
        self, validator: TodoTemplateDefinitionValidator | None = None
    ) -> None:
        self._validator = validator or TodoTemplateDefinitionValidator()

    def encode(
        self, definition: TodoTemplateDefinition
    ) -> EncodedTodoTemplateDefinition:
        normalized = self._validator.validate(definition)
        definition_json = self._canonical_json(self._to_primitive(normalized))
        return EncodedTodoTemplateDefinition(
            definition_json=definition_json,
            definition_hash=self._digest(definition_json),
        )

    def decode(self, definition_json: str) -> TodoTemplateDefinition:
        try:
            value = json.loads(definition_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TodoTemplateValidationError(
                "persisted template definition must be valid JSON"
            ) from exc
        definition = self._from_primitive(value)
        return self._validator.validate(definition)

    def hash_definition(self, definition: TodoTemplateDefinition) -> str:
        return self.encode(definition).definition_hash

    def hash_json(self, definition_json: str) -> str:
        try:
            value = json.loads(definition_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TodoTemplateValidationError(
                "persisted template definition must be valid JSON"
            ) from exc
        return self._digest(self._canonical_json(value))

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_reference(reference: TodoParticipantReference) -> dict[str, Any]:
        result: dict[str, Any] = {"source": reference.source.value}
        if reference.key is not None:
            result["key"] = reference.key
        if reference.subject_type is not None:
            result["subject_type"] = reference.subject_type.value
        if reference.subject_id is not None:
            result["subject_id"] = reference.subject_id
        return result

    @classmethod
    def _to_primitive(cls, definition: TodoTemplateDefinition) -> dict[str, Any]:
        return {
            "schema_version": definition.schema_version,
            "name": definition.name,
            "description": definition.description,
            "binding_schema": [
                {
                    "key": participant.key,
                    "label": participant.label,
                    "subject_type": participant.subject_type.value,
                    "required_at": participant.required_at.value,
                }
                for participant in definition.binding_schema
            ],
            "stages": [
                {
                    "key": stage.key,
                    "name": stage.name,
                    "purpose": stage.purpose,
                    "stage_kind": stage.stage_kind.value,
                    "executor_ref": cls._to_reference(stage.executor_ref),
                    **(
                        {"owner_ref": cls._to_reference(stage.owner_ref)}
                        if stage.owner_ref is not None
                        else {}
                    ),
                    "completion_gate": {
                        "required_artifacts": [
                            {
                                "type": gate.artifact_type,
                                "state": gate.artifact_state,
                                "min_count": gate.min_count,
                            }
                            for gate in stage.completion_gate.required_artifacts
                        ]
                    },
                }
                for stage in definition.stages
            ],
        }

    @classmethod
    def _from_primitive(cls, value: Any) -> TodoTemplateDefinition:
        root = cls._mapping(value, "template definition")
        cls._fields(
            root,
            required={
                "schema_version",
                "name",
                "description",
                "binding_schema",
                "stages",
            },
            optional=set(),
            label="template definition",
        )
        binding_schema = cls._list(root["binding_schema"], "binding_schema")
        stages = cls._list(root["stages"], "stages")
        return TodoTemplateDefinition(
            schema_version=cls._string(root["schema_version"], "schema_version"),
            name=cls._string(root["name"], "name"),
            description=cls._string(root["description"], "description"),
            binding_schema=tuple(
                cls._participant(item, index)
                for index, item in enumerate(binding_schema)
            ),
            stages=tuple(cls._stage(item, index) for index, item in enumerate(stages)),
        )

    @classmethod
    def _participant(cls, value: Any, index: int) -> TodoParticipantDefinition:
        label = f"binding_schema[{index}]"
        item = cls._mapping(value, label)
        cls._fields(
            item,
            required={"key", "label", "subject_type", "required_at"},
            optional=set(),
            label=label,
        )
        return TodoParticipantDefinition(
            key=cls._string(item["key"], f"{label}.key"),
            label=cls._string(item["label"], f"{label}.label"),
            subject_type=cls._enum(
                TodoParticipantSubjectType,
                item["subject_type"],
                f"{label}.subject_type",
            ),
            required_at=cls._enum(
                TodoParticipantRequiredAt,
                item["required_at"],
                f"{label}.required_at",
            ),
        )

    @classmethod
    def _stage(cls, value: Any, index: int) -> TodoStageDefinition:
        label = f"stages[{index}]"
        item = cls._mapping(value, label)
        cls._fields(
            item,
            required={
                "key",
                "name",
                "purpose",
                "stage_kind",
                "executor_ref",
                "completion_gate",
            },
            optional={"owner_ref"},
            label=label,
        )
        completion = cls._mapping(item["completion_gate"], f"{label}.completion_gate")
        cls._fields(
            completion,
            required={"required_artifacts"},
            optional=set(),
            label=f"{label}.completion_gate",
        )
        artifacts = cls._list(
            completion["required_artifacts"],
            f"{label}.completion_gate.required_artifacts",
        )
        return TodoStageDefinition(
            key=cls._string(item["key"], f"{label}.key"),
            name=cls._string(item["name"], f"{label}.name"),
            purpose=cls._string(item["purpose"], f"{label}.purpose"),
            stage_kind=cls._enum(
                TodoStageKind, item["stage_kind"], f"{label}.stage_kind"
            ),
            executor_ref=cls._reference(item["executor_ref"], f"{label}.executor_ref"),
            owner_ref=(
                cls._reference(item["owner_ref"], f"{label}.owner_ref")
                if "owner_ref" in item
                else None
            ),
            completion_gate=TodoCompletionGate(
                required_artifacts=tuple(
                    cls._artifact_gate(artifact, label, artifact_index)
                    for artifact_index, artifact in enumerate(artifacts)
                )
            ),
        )

    @classmethod
    def _reference(cls, value: Any, label: str) -> TodoParticipantReference:
        item = cls._mapping(value, label)
        cls._fields(
            item,
            required={"source"},
            optional={"key", "subject_type", "subject_id"},
            label=f"{label} participant reference",
        )
        return TodoParticipantReference(
            source=cls._enum(
                TodoParticipantRefSource, item["source"], f"{label}.source"
            ),
            key=cls._optional_string(item.get("key"), f"{label}.key"),
            subject_type=(
                cls._enum(
                    TodoParticipantSubjectType,
                    item["subject_type"],
                    f"{label}.subject_type",
                )
                if "subject_type" in item
                else None
            ),
            subject_id=cls._optional_string(
                item.get("subject_id"), f"{label}.subject_id"
            ),
        )

    @classmethod
    def _artifact_gate(
        cls, value: Any, stage_label: str, index: int
    ) -> TodoRequiredArtifactGate:
        label = f"{stage_label}.completion_gate.required_artifacts[{index}]"
        item = cls._mapping(value, label)
        cls._fields(
            item,
            required={"type", "state", "min_count"},
            optional=set(),
            label=label,
        )
        min_count = item["min_count"]
        if not isinstance(min_count, int) or isinstance(min_count, bool):
            raise TodoTemplateValidationError(f"{label}.min_count must be an integer")
        return TodoRequiredArtifactGate(
            artifact_type=cls._string(item["type"], f"{label}.type"),
            artifact_state=cls._string(item["state"], f"{label}.state"),
            min_count=min_count,
        )

    @staticmethod
    def _mapping(value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TodoTemplateValidationError(f"{label} must be an object")
        if not all(isinstance(key, str) for key in value):
            raise TodoTemplateValidationError(f"{label} field names must be text")
        return value

    @staticmethod
    def _list(value: Any, label: str) -> list[Any]:
        if not isinstance(value, list):
            raise TodoTemplateValidationError(f"{label} must be an array")
        return value

    @staticmethod
    def _fields(
        value: dict[str, Any],
        *,
        required: set[str],
        optional: set[str],
        label: str,
    ) -> None:
        unknown = sorted(set(value) - required - optional)
        if unknown:
            raise TodoTemplateValidationError(
                f"unknown {label} field(s): {', '.join(unknown)}"
            )
        missing = sorted(required - set(value))
        if missing:
            raise TodoTemplateValidationError(
                f"missing {label} field(s): {', '.join(missing)}"
            )

    @staticmethod
    def _string(value: Any, label: str) -> str:
        if not isinstance(value, str):
            raise TodoTemplateValidationError(f"{label} must be text")
        return value

    @classmethod
    def _optional_string(cls, value: Any, label: str) -> str | None:
        return None if value is None else cls._string(value, label)

    @staticmethod
    def _enum(enum_type, value: Any, label: str):
        if not isinstance(value, str):
            raise TodoTemplateValidationError(f"{label} must be text")
        try:
            return enum_type(value)
        except ValueError as exc:
            raise TodoTemplateValidationError(
                f"{label} has unsupported value {value!r}"
            ) from exc
