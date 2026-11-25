from __future__ import annotations

from dataclasses import replace

import pytest

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


def _participant(
    key: str,
    subject_type: TodoParticipantSubjectType,
    *,
    required_at: TodoParticipantRequiredAt
    | str = TodoParticipantRequiredAt.TODO_CREATE,
) -> TodoParticipantDefinition:
    return TodoParticipantDefinition(
        key=key,
        label=key.replace("_", " ").title(),
        subject_type=subject_type,
        required_at=required_at,
    )


def _binding(key: str) -> TodoParticipantReference:
    return TodoParticipantReference(
        source=TodoParticipantRefSource.BINDING,
        key=key,
    )


def _fixed(
    subject_type: TodoParticipantSubjectType,
    subject_id: str,
) -> TodoParticipantReference:
    return TodoParticipantReference(
        source=TodoParticipantRefSource.FIXED,
        subject_type=subject_type,
        subject_id=subject_id,
    )


def _bot_stage(**overrides) -> TodoStageDefinition:
    values = {
        "key": "development",
        "name": "Code development",
        "purpose": "Implement the Todo and produce a reviewable change.",
        "stage_kind": TodoStageKind.BOT,
        "executor_ref": _binding("developer_bot"),
        "owner_ref": _binding("technical_owner"),
        "completion_gate": TodoCompletionGate(),
    }
    values.update(overrides)
    return TodoStageDefinition(**values)


def _human_stage(**overrides) -> TodoStageDefinition:
    values = {
        "key": "release_confirmation",
        "name": "Release confirmation",
        "purpose": "Confirm that deployment verification may start.",
        "stage_kind": TodoStageKind.HUMAN,
        "executor_ref": _binding("release_owner"),
        "owner_ref": None,
        "completion_gate": TodoCompletionGate(),
    }
    values.update(overrides)
    return TodoStageDefinition(**values)


def _definition(**overrides) -> TodoTemplateDefinition:
    values = {
        "schema_version": "1.0",
        "name": "Standard delivery",
        "description": "Clarify, implement, and verify a Todo.",
        "binding_schema": (
            _participant("developer_bot", TodoParticipantSubjectType.BOT),
            _participant("technical_owner", TodoParticipantSubjectType.HUMAN),
            _participant("release_owner", TodoParticipantSubjectType.HUMAN),
        ),
        "stages": (_bot_stage(),),
    }
    values.update(overrides)
    return TodoTemplateDefinition(**values)


def _validate(definition: TodoTemplateDefinition) -> TodoTemplateDefinition:
    return TodoTemplateDefinitionValidator().validate(definition)


def test_rejects_template_without_stages() -> None:
    with pytest.raises(TodoTemplateValidationError, match="at least one stage"):
        _validate(_definition(stages=()))


def test_rejects_duplicate_participant_keys() -> None:
    duplicate = _participant("developer_bot", TodoParticipantSubjectType.BOT)

    with pytest.raises(TodoTemplateValidationError, match="duplicate participant key"):
        _validate(
            _definition(
                binding_schema=(
                    duplicate,
                    replace(duplicate, label="Another developer Bot"),
                )
            )
        )


def test_rejects_duplicate_stage_keys() -> None:
    stage = _bot_stage()

    with pytest.raises(TodoTemplateValidationError, match="duplicate stage key"):
        _validate(_definition(stages=(stage, replace(stage, name="Duplicate"))))


def test_rejects_binding_reference_to_unknown_participant() -> None:
    with pytest.raises(TodoTemplateValidationError, match="unknown participant"):
        _validate(
            _definition(stages=(_bot_stage(executor_ref=_binding("missing_bot")),))
        )


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        (
            _bot_stage(executor_ref=_binding("release_owner")),
            "bot stage executor",
        ),
        (
            _human_stage(executor_ref=_binding("developer_bot")),
            "human stage executor",
        ),
    ],
)
def test_stage_executor_subject_type_must_match_stage_kind(
    stage: TodoStageDefinition,
    message: str,
) -> None:
    with pytest.raises(TodoTemplateValidationError, match=message):
        _validate(_definition(stages=(stage,)))


def test_bot_stage_requires_human_owner() -> None:
    with pytest.raises(TodoTemplateValidationError, match="bot stage owner"):
        _validate(_definition(stages=(_bot_stage(owner_ref=None),)))


@pytest.mark.parametrize(
    "owner_ref",
    [
        _binding("developer_bot"),
        _fixed(TodoParticipantSubjectType.BOT, "bot-fixed"),
    ],
)
def test_stage_owner_must_resolve_to_human(
    owner_ref: TodoParticipantReference,
) -> None:
    with pytest.raises(TodoTemplateValidationError, match="owner.*human"):
        _validate(_definition(stages=(_bot_stage(owner_ref=owner_ref),)))


def test_human_stage_defaults_owner_to_executor() -> None:
    stage = _human_stage(owner_ref=None)

    normalized = _validate(_definition(stages=(stage,)))

    assert normalized.stages[0].owner_ref == stage.executor_ref


def test_rejects_unknown_hard_artifact_gate() -> None:
    stage = _bot_stage(
        completion_gate=TodoCompletionGate(
            required_artifacts=(
                TodoRequiredArtifactGate(
                    artifact_type="deployment",
                    artifact_state="verified",
                ),
            )
        )
    )

    with pytest.raises(
        TodoTemplateValidationError, match="unsupported hard artifact gate"
    ):
        _validate(_definition(stages=(stage,)))


def test_accepts_pull_request_created_hard_gate() -> None:
    stage = _bot_stage(
        completion_gate=TodoCompletionGate(
            required_artifacts=(
                TodoRequiredArtifactGate(
                    artifact_type="pull_request",
                    artifact_state="created",
                    min_count=1,
                ),
            )
        )
    )

    normalized = _validate(_definition(stages=(stage,)))

    assert normalized.stages[0].completion_gate.required_artifacts == (
        TodoRequiredArtifactGate(
            artifact_type="pull_request",
            artifact_state="created",
            min_count=1,
        ),
    )


@pytest.mark.parametrize(
    "reference",
    [
        TodoParticipantReference(source=TodoParticipantRefSource.BINDING),
        TodoParticipantReference(
            source=TodoParticipantRefSource.BINDING,
            key="developer_bot",
            subject_id="unexpected",
        ),
    ],
)
def test_binding_reference_requires_only_participant_key(
    reference: TodoParticipantReference,
) -> None:
    with pytest.raises(TodoTemplateValidationError, match="binding reference"):
        _validate(_definition(stages=(_bot_stage(executor_ref=reference),)))


@pytest.mark.parametrize(
    "reference",
    [
        TodoParticipantReference(
            source=TodoParticipantRefSource.FIXED,
            subject_type=TodoParticipantSubjectType.BOT,
        ),
        TodoParticipantReference(
            source=TodoParticipantRefSource.FIXED,
            subject_id="bot-fixed",
        ),
        TodoParticipantReference(
            source=TodoParticipantRefSource.FIXED,
            key="developer_bot",
            subject_type=TodoParticipantSubjectType.BOT,
            subject_id="bot-fixed",
        ),
    ],
)
def test_fixed_reference_requires_only_subject_type_and_subject_id(
    reference: TodoParticipantReference,
) -> None:
    with pytest.raises(TodoTemplateValidationError, match="fixed reference"):
        _validate(_definition(stages=(_bot_stage(executor_ref=reference),)))


def test_todo_creator_reference_is_only_valid_for_human_owner() -> None:
    creator = TodoParticipantReference(source=TodoParticipantRefSource.TODO_CREATOR)

    with pytest.raises(TodoTemplateValidationError, match="todo_creator"):
        _validate(_definition(stages=(_human_stage(executor_ref=creator),)))

    normalized = _validate(_definition(stages=(_bot_stage(owner_ref=creator),)))
    assert normalized.stages[0].owner_ref == creator


def test_required_participants_must_bind_at_todo_create() -> None:
    participant = _participant(
        "developer_bot",
        TodoParticipantSubjectType.BOT,
        required_at="stage_start",
    )

    with pytest.raises(TodoTemplateValidationError, match="required_at.*todo_create"):
        _validate(
            _definition(
                binding_schema=(
                    participant,
                    _participant(
                        "technical_owner",
                        TodoParticipantSubjectType.HUMAN,
                    ),
                )
            )
        )
