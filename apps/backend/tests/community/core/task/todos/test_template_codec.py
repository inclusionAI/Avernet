from __future__ import annotations

from dataclasses import replace
import json

import pytest

from agentclaw.community.core.task.todos.errors import TodoTemplateValidationError
from agentclaw.community.core.task.todos.template_codec import (
    TodoTemplateDefinitionCodec,
)
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


def _definition() -> TodoTemplateDefinition:
    return TodoTemplateDefinition(
        schema_version="1.0",
        name="Standard delivery",
        description="Clarify and implement a Todo.",
        binding_schema=(
            TodoParticipantDefinition(
                key="developer_bot",
                label="Developer Bot",
                subject_type=TodoParticipantSubjectType.BOT,
                required_at=TodoParticipantRequiredAt.TODO_CREATE,
            ),
            TodoParticipantDefinition(
                key="technical_owner",
                label="Technical Owner",
                subject_type=TodoParticipantSubjectType.HUMAN,
                required_at=TodoParticipantRequiredAt.TODO_CREATE,
            ),
        ),
        stages=(
            TodoStageDefinition(
                key="development",
                name="Code development",
                purpose="Implement the Todo and create a reviewable change.",
                stage_kind=TodoStageKind.BOT,
                executor_ref=TodoParticipantReference(
                    source=TodoParticipantRefSource.BINDING,
                    key="developer_bot",
                ),
                owner_ref=TodoParticipantReference(
                    source=TodoParticipantRefSource.BINDING,
                    key="technical_owner",
                ),
                completion_gate=TodoCompletionGate(
                    required_artifacts=(
                        TodoRequiredArtifactGate(
                            artifact_type="pull_request",
                            artifact_state="created",
                            min_count=1,
                        ),
                    )
                ),
            ),
        ),
    )


def test_canonical_codec_round_trips_and_uses_documented_gate_keys() -> None:
    codec = TodoTemplateDefinitionCodec()

    encoded = codec.encode(_definition())
    decoded = codec.decode(encoded.definition_json)

    assert decoded == _definition()
    assert encoded.definition_hash == codec.hash_definition(decoded)
    primitive = json.loads(encoded.definition_json)
    gate = primitive["stages"][0]["completion_gate"]["required_artifacts"][0]
    assert gate == {"min_count": 1, "state": "created", "type": "pull_request"}


def test_hash_is_stable_for_mapping_key_order_but_stage_order_is_semantic() -> None:
    codec = TodoTemplateDefinitionCodec()
    definition = _definition()
    encoded = codec.encode(definition)
    reordered_json = json.dumps(
        json.loads(encoded.definition_json),
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    )

    assert codec.hash_json(reordered_json) == encoded.definition_hash

    second = replace(
        definition.stages[0],
        key="verification",
        name="Verification",
        completion_gate=TodoCompletionGate(),
    )
    forward = replace(definition, stages=(definition.stages[0], second))
    reversed_definition = replace(definition, stages=(second, definition.stages[0]))

    assert codec.hash_definition(forward) != codec.hash_definition(reversed_definition)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (
            lambda value: value.update({"unexpected": True}),
            "unknown template definition field",
        ),
        (
            lambda value: value["stages"][0].update({"stage_kind": "robot"}),
            "stage_kind",
        ),
        (
            lambda value: value["stages"][0]["executor_ref"].update(
                {"subject_id": "not-allowed"}
            ),
            "binding reference",
        ),
    ],
)
def test_decode_rejects_unknown_or_invalid_persisted_definition(
    mutate, message
) -> None:
    codec = TodoTemplateDefinitionCodec()
    primitive = json.loads(codec.encode(_definition()).definition_json)
    mutate(primitive)

    with pytest.raises(TodoTemplateValidationError, match=message):
        codec.decode(json.dumps(primitive))
