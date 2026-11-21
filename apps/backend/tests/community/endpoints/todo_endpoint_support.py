"""Shared fixtures for declarative Todo endpoint coverage cases."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.stage_models import (
    TodoPendingActionKind,
    TodoStageRecord,
    TodoStageStatus,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateDetail,
    TodoTemplatePage,
    TodoTemplateRecord,
    TodoTemplateStatus,
    TodoTemplateVersionRecord,
    TodoTemplateVersionStatus,
)
from agentclaw.community.core.task.todos.template_models import (
    TodoCompletionGate,
    TodoParticipantDefinition,
    TodoParticipantReference,
    TodoParticipantRefSource,
    TodoParticipantRequiredAt,
    TodoParticipantSubjectType,
    TodoStageDefinition,
    TodoStageKind,
    TodoTemplateDefinition,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)

USER_ID = "todo-template-endpoint-user"
OTHER_USER_ID = "todo-template-other-user"
SPACE_ID = 1
BOT_ID = "todo-template-bot"
TEMPLATE_ID = "todo_tpl_1"
VERSION_ID = "todo_tplv_1"
TODO_ID = "todo_template_1"
STAGE_ID = "todostage_1"
NOW = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
_SIGNING_KEY = "todo-template-endpoint-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def principal_headers() -> dict[str, str]:
    now = int(time.time())
    principal_value = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60 * 60,
            "principals": [
                {
                    "type": "user",
                    "tenant": "todo-template-endpoint-test",
                    "subject": {
                        "id": USER_ID,
                        "username": "todo-template@example.com",
                    },
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {PRINCIPAL_HEADER: principal_value}


def enable_public_auth(_world: Any) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def template_definition(name: str = "Standard delivery") -> TodoTemplateDefinition:
    return TodoTemplateDefinition(
        schema_version="1.0",
        name=name,
        description="Implement and verify the Todo.",
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
                name="Development",
                purpose="Implement the Todo and create a pull request.",
                stage_kind=TodoStageKind.BOT,
                executor_ref=TodoParticipantReference(
                    source=TodoParticipantRefSource.BINDING,
                    key="developer_bot",
                ),
                owner_ref=TodoParticipantReference(
                    source=TodoParticipantRefSource.BINDING,
                    key="technical_owner",
                ),
                completion_gate=TodoCompletionGate(),
            ),
        ),
    )


def template_definition_payload(
    name: str = "Standard delivery",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "name": name,
        "description": "Implement and verify the Todo.",
        "binding_schema": [
            {
                "key": "developer_bot",
                "label": "Developer Bot",
                "subject_type": "bot",
                "required_at": "todo_create",
            },
            {
                "key": "technical_owner",
                "label": "Technical Owner",
                "subject_type": "human",
                "required_at": "todo_create",
            },
        ],
        "stages": [
            {
                "key": "development",
                "name": "Development",
                "purpose": "Implement the Todo and create a pull request.",
                "stage_kind": "bot",
                "executor_ref": {"source": "binding", "key": "developer_bot"},
                "owner_ref": {"source": "binding", "key": "technical_owner"},
                "completion_gate": {"required_artifacts": []},
            }
        ],
    }


def template_record(
    *, status: TodoTemplateStatus = TodoTemplateStatus.ACTIVE
) -> TodoTemplateRecord:
    return TodoTemplateRecord(
        template_id=TEMPLATE_ID,
        space_id=SPACE_ID,
        name="Standard delivery",
        description="Implement and verify the Todo.",
        status=status,
        published_version_id=(
            VERSION_ID if status is TodoTemplateStatus.ACTIVE else None
        ),
        created_by=USER_ID,
        updated_by=USER_ID,
        created_at=NOW,
        updated_at=NOW,
    )


def template_version(
    *, status: TodoTemplateVersionStatus = TodoTemplateVersionStatus.DRAFT
) -> TodoTemplateVersionRecord:
    return TodoTemplateVersionRecord(
        template_version_id=VERSION_ID,
        template_id=TEMPLATE_ID,
        version_no=1,
        status=status,
        schema_version="1.0",
        definition=template_definition(),
        definition_hash="a" * 64,
        created_by=USER_ID,
        published_by=(USER_ID if status is TodoTemplateVersionStatus.PUBLISHED else None),
        published_at=(NOW if status is TodoTemplateVersionStatus.PUBLISHED else None),
        created_at=NOW,
        updated_at=NOW,
    )


def template_detail() -> TodoTemplateDetail:
    return TodoTemplateDetail(template_record(), (template_version(),))


def template_page() -> TodoTemplatePage:
    return TodoTemplatePage(total=1, items=(template_record(),))


def todo_record(
    *, status: TodoStatus = TodoStatus.NOT_STARTED
) -> TodoRecord:
    return TodoRecord(
        todo_id=TODO_ID,
        space_id=SPACE_ID,
        title="Template Todo",
        description="Execute the standard delivery flow.",
        bot_id=BOT_ID,
        bot_name="Todo Template Bot",
        status=status,
        collab_context_id=(
            "context_template_1" if status is not TodoStatus.NOT_STARTED else None
        ),
        created_by=USER_ID,
        created_at=NOW,
        updated_at=NOW,
        template_id=TEMPLATE_ID,
        template_version_id=VERSION_ID,
        template_name_snapshot="Standard delivery",
        current_stage_instance_id=(
            None if status in {TodoStatus.COMPLETED, TodoStatus.CANCELLED} else STAGE_ID
        ),
    )


def stage_record() -> TodoStageRecord:
    return TodoStageRecord(
        stage_instance_id=STAGE_ID,
        todo_id=TODO_ID,
        template_version_id=VERSION_ID,
        stage_key="human_review",
        stage_order=2,
        stage_name="Human review",
        purpose="Review the implementation before deployment.",
        stage_kind="human",
        executor_binding_key="technical_owner",
        executor_subject_type="human",
        executor_subject_id=USER_ID,
        executor_subject_name="Technical Owner",
        owner_binding_key="technical_owner",
        owner_user_id=USER_ID,
        owner_user_name="Technical Owner",
        completion_gate_json='{"required_artifacts": []}',
        status=TodoStageStatus.IN_PROGRESS,
        collab_context_id=None,
        pending_action_kind=TodoPendingActionKind.PLANNED_HUMAN_STAGE,
        state_reason=None,
        requested_action=None,
        started_at=NOW,
        completed_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
