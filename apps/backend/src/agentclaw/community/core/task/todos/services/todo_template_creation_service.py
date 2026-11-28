"""Expand a published product-flow template into an executable Todo."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from uuid import uuid4

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.task.todo_templates import (
    TodoTemplateRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.todos.bot_scope import (
    resolve_todo_bot,
    todo_bot_name,
)
from agentclaw.community.core.task.todos.errors import (
    TodoTemplateStateConflictError,
    TodoTemplateValidationError,
)
from agentclaw.community.core.task.todos.models import (
    MAX_ACTOR_ID_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.core.task.todos.source_models import TodoSourceInput
from agentclaw.community.core.task.todos.stage_models import (
    TodoParticipantBinding,
    TodoParticipantBindingInput,
    TodoPendingActionKind,
    TodoStageRecord,
    TodoStageStatus,
)
from agentclaw.community.core.task.todos.template_creation_protocol import (
    TodoTemplateCreationServiceProtocol,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateStatus,
    TodoTemplateVersionStatus,
)
from agentclaw.community.core.task.todos.template_models import (
    TodoParticipantRefSource,
    TodoParticipantSubjectType,
    TodoStageKind,
)


class TodoTemplateCreationService(TodoTemplateCreationServiceProtocol):
    """Validate bindings, snapshot identities, and persist one stage graph."""

    @inject
    def __init__(
        self,
        repository: TodoRepositoryProtocol,
        template_repository: TodoTemplateRepositoryProtocol,
        space_access: SpaceAccessServiceProtocol,
        bot_repository: BotRepository,
    ) -> None:
        self._repository = repository
        self._templates = template_repository
        self._space_access = space_access
        self._bot_repository = bot_repository

    def create_todo_from_template(
        self,
        *,
        space_id: int,
        actor_id: str,
        template_id: str,
        template_version_id: str,
        title: str,
        description: str,
        bindings: Mapping[str, object],
        source: TodoSourceInput | None = None,
        env: str | None = None,
    ) -> TodoRecord:
        space_id = self._positive_int(space_id, "space_id")
        actor_id = self._text(actor_id, "actor_id", MAX_ACTOR_ID_LENGTH)
        title = self._text(title, "title", MAX_TITLE_LENGTH)
        description = self._text_allow_empty(
            description, "description", MAX_DESCRIPTION_LENGTH
        )
        template_id = self._text(template_id, "template_id", 64)
        template_version_id = self._text(template_version_id, "template_version_id", 64)
        if not isinstance(bindings, Mapping):
            raise ValidationError("participant_bindings must be an object")

        template = self._templates.get_template(template_id=template_id)
        if template.space_id != space_id:
            raise TodoTemplateValidationError("template does not belong to space")
        if template.status is not TodoTemplateStatus.ACTIVE:
            raise TodoTemplateStateConflictError("archived Todo template cannot create Todos")
        version = self._templates.get_version(
            template_id=template_id,
            template_version_id=template_version_id,
        )
        if version.status is not TodoTemplateVersionStatus.PUBLISHED:
            raise TodoTemplateStateConflictError(
                "Todo creation requires a published template version"
            )

        space, _member = self._space_access.require_space_member(
            space_id=space_id,
            user_id=actor_id,
            env=env,
        )
        concrete = self._resolve_bindings(
            definition=version.definition,
            bindings=bindings,
            space=space,
            actor_id=actor_id,
        )
        stages = self._build_stages(
            definition=version.definition,
            template_version_id=version.template_version_id,
            todo_id=f"todo_{uuid4().hex}",
            concrete=concrete,
            actor_id=actor_id,
            space=space,
        )
        first = stages[0]
        first_is_human = first.stage_kind == TodoStageKind.HUMAN.value
        now = datetime.now(timezone.utc)
        todo_id = first.todo_id
        todo = TodoRecord(
            todo_id=todo_id,
            space_id=space_id,
            title=title,
            description=description,
            bot_id=first.executor_subject_id if not first_is_human else "",
            bot_name=first.executor_subject_name if not first_is_human else "",
            status=(TodoStatus.PENDING_ACTION if first_is_human else TodoStatus.NOT_STARTED),
            collab_context_id=None,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
            template_id=template.template_id,
            template_version_id=version.template_version_id,
            template_name_snapshot=template.name,
            current_stage_instance_id=first.stage_instance_id,
            pending_action_kind=(
                first.pending_action_kind.value
                if first.pending_action_kind is not None
                else None
            ),
            state_reason=first.state_reason,
            requested_action=first.requested_action,
        )
        return self._repository.create_todo_with_stages(
            record=todo,
            participant_bindings=tuple(concrete.values()),
            stages=stages,
            source=source,
        )

    def _resolve_bindings(
        self, *, definition, bindings: Mapping[str, object], space, actor_id: str
    ) -> dict[str, TodoParticipantBinding]:
        expected = {item.key: item for item in definition.binding_schema}
        unknown = set(bindings) - set(expected)
        if unknown:
            raise TodoTemplateValidationError(
                "unknown participant binding(s): " + ", ".join(sorted(unknown))
            )
        result: dict[str, TodoParticipantBinding] = {}
        for item in definition.binding_schema:
            raw = bindings.get(item.key)
            if raw is None:
                raise TodoTemplateValidationError(
                    f"required participant binding {item.key!r} is missing"
                )
            value = self._binding_input(raw, item.key)
            if value.subject_type != item.subject_type.value:
                raise TodoTemplateValidationError(
                    f"participant {item.key!r} must resolve to {item.subject_type.value}"
                )
            subject_name = value.subject_name or value.subject_id
            if item.subject_type is TodoParticipantSubjectType.BOT:
                bot = resolve_todo_bot(
                    bot_repository=self._bot_repository,
                    space=space,
                    actor_id=actor_id,
                    bot_id=value.subject_id,
                )
                subject_name = todo_bot_name(bot, value.subject_id)
            result[item.key] = TodoParticipantBinding(
                key=item.key,
                label=item.label,
                subject_type=value.subject_type,
                subject_id=value.subject_id,
                subject_name=subject_name,
            )
        return result

    def _build_stages(
        self, *, definition, template_version_id: str, todo_id: str,
        concrete: dict[str, TodoParticipantBinding], actor_id: str, space
    ) -> tuple[TodoStageRecord, ...]:
        stages: list[TodoStageRecord] = []
        now = datetime.now(timezone.utc)
        for index, stage in enumerate(definition.stages):
            executor_key, executor = self._resolve_reference(
                stage.executor_ref, concrete=concrete, actor_id=actor_id,
                space=space,
            )
            owner_key, owner = self._resolve_reference(
                stage.owner_ref, concrete=concrete, actor_id=actor_id,
                space=space,
            )
            if owner.subject_type != TodoParticipantSubjectType.HUMAN.value:
                raise TodoTemplateValidationError(
                    f"stage {stage.key!r} owner must resolve to a human"
                )
            is_first = index == 0
            is_human = stage.stage_kind is TodoStageKind.HUMAN
            status = (
                TodoStageStatus.IN_PROGRESS
                if is_first and is_human
                else TodoStageStatus.NOT_STARTED
            )
            stages.append(
                TodoStageRecord(
                    stage_instance_id=f"todo_stage_{uuid4().hex}",
                    todo_id=todo_id,
                    template_version_id=template_version_id,
                    stage_key=stage.key,
                    stage_order=index,
                    stage_name=stage.name,
                    purpose=stage.purpose,
                    stage_kind=stage.stage_kind.value,
                    executor_binding_key=executor_key,
                    executor_subject_type=executor.subject_type,
                    executor_subject_id=executor.subject_id,
                    executor_subject_name=executor.subject_name,
                    owner_binding_key=owner_key,
                    owner_user_id=owner.subject_id,
                    owner_user_name=owner.subject_name,
                    completion_gate_json=json.dumps(
                        {
                            "required_artifacts": [
                                {
                                    "type": gate.artifact_type,
                                    "state": gate.artifact_state,
                                    "min_count": gate.min_count,
                                }
                                for gate in stage.completion_gate.required_artifacts
                            ]
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    status=status,
                    collab_context_id=None,
                    pending_action_kind=(
                        TodoPendingActionKind.PLANNED_HUMAN_STAGE
                        if is_first and is_human
                        else None
                    ),
                    state_reason=(
                        "planned human stage requires action"
                        if is_first and is_human
                        else None
                    ),
                    requested_action=None,
                    started_at=now if is_first and is_human else None,
                    completed_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        return tuple(stages)

    def _resolve_reference(self, reference, *, concrete, actor_id, space):
        if reference is None:
            raise TodoTemplateValidationError("stage owner reference is required")
        if reference.source is TodoParticipantRefSource.BINDING:
            assert reference.key is not None
            binding = concrete[reference.key]
            return reference.key, binding
        if reference.source is TodoParticipantRefSource.TODO_CREATOR:
            return None, TodoParticipantBinding(
                key="todo_creator",
                label="Todo creator",
                subject_type=TodoParticipantSubjectType.HUMAN.value,
                subject_id=actor_id,
                subject_name=actor_id,
            )
        assert reference.subject_type is not None and reference.subject_id is not None
        subject_name = reference.subject_id
        if reference.subject_type is TodoParticipantSubjectType.BOT:
            bot = resolve_todo_bot(
                bot_repository=self._bot_repository,
                space=space,
                actor_id=actor_id,
                bot_id=reference.subject_id,
            )
            subject_name = todo_bot_name(bot, reference.subject_id)
        return None, TodoParticipantBinding(
            key="fixed",
            label="Fixed participant",
            subject_type=reference.subject_type.value,
            subject_id=reference.subject_id,
            subject_name=subject_name,
        )

    @staticmethod
    def _binding_input(value: object, key: str) -> TodoParticipantBindingInput:
        if isinstance(value, TodoParticipantBindingInput):
            result = value
        elif isinstance(value, Mapping):
            try:
                result = TodoParticipantBindingInput(
                    subject_type=str(value["subject_type"]),
                    subject_id=str(value["subject_id"]),
                    subject_name=(
                        str(value["subject_name"])
                        if value.get("subject_name") is not None
                        else None
                    ),
                )
            except (KeyError, TypeError) as exc:
                raise TodoTemplateValidationError(
                    f"participant binding {key!r} requires subject_type and subject_id"
                ) from exc
        else:
            raise TodoTemplateValidationError(f"participant binding {key!r} is invalid")
        if not result.subject_type.strip() or not result.subject_id.strip():
            raise TodoTemplateValidationError(
                f"participant binding {key!r} requires non-empty identity"
            )
        return result

    @staticmethod
    def _positive_int(value: object, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValidationError(f"{field} must be a positive integer")
        return value

    @staticmethod
    def _text(value: object, field: str, max_length: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field} must not be empty")
        value = value.strip()
        if len(value) > max_length:
            raise ValidationError(f"{field} exceeds the maximum length")
        return value

    @classmethod
    def _text_allow_empty(cls, value: object, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        value = value.strip()
        if len(value) > max_length:
            raise ValidationError(f"{field} exceeds the maximum length")
        return value
