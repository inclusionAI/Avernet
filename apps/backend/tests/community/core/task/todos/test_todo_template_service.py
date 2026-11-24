from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.task.todos.errors import TodoTemplateStateConflictError
from agentclaw.community.core.task.todos.services.todo_template_service import (
    TodoTemplateService,
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

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)


def _definition(name: str = "Standard delivery") -> TodoTemplateDefinition:
    return TodoTemplateDefinition(
        schema_version="1.0",
        name=name,
        description="Implement the Todo.",
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
                purpose="Implement the Todo.",
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


def _template(status: TodoTemplateStatus = TodoTemplateStatus.ACTIVE):
    return TodoTemplateRecord(
        template_id="todo_tpl_1",
        space_id=7,
        name="Standard delivery",
        description="Implement the Todo.",
        status=status,
        published_version_id=None,
        created_by="admin_1",
        updated_by="admin_1",
        created_at=NOW,
        updated_at=NOW,
    )


def _version(status: TodoTemplateVersionStatus = TodoTemplateVersionStatus.DRAFT):
    return TodoTemplateVersionRecord(
        template_version_id="todo_tplv_1",
        template_id="todo_tpl_1",
        version_no=1,
        status=status,
        schema_version="1.0",
        definition=_definition(),
        definition_hash="a" * 64,
        created_by="admin_1",
        published_by="admin_1"
        if status is TodoTemplateVersionStatus.PUBLISHED
        else None,
        published_at=NOW if status is TodoTemplateVersionStatus.PUBLISHED else None,
        created_at=NOW,
        updated_at=NOW,
    )


class _Access:
    def __init__(self, *, role: SpaceRole = SpaceRole.ADMIN) -> None:
        self.role = role
        self.member_calls: list[dict[str, Any]] = []

    def require_space_member(self, **kwargs):
        self.member_calls.append(kwargs)
        return (
            SimpleNamespace(id=kwargs["space_id"], created_by="creator_1"),
            SimpleNamespace(role=self.role),
        )


class _Repository:
    def __init__(self, *, status: TodoTemplateStatus = TodoTemplateStatus.ACTIVE):
        self.template = _template(status)
        self.version = _version()
        self.calls: list[dict[str, Any]] = []

    def _call(self, operation: str, **kwargs):
        self.calls.append({"operation": operation, **kwargs})

    def create_template(self, **kwargs):
        self._call("create_template", **kwargs)
        self.template = replace(
            self.template,
            space_id=kwargs["space_id"],
            name=kwargs["definition"].name,
            description=kwargs["definition"].description,
            created_by=kwargs["created_by"],
            updated_by=kwargs["created_by"],
        )
        self.version = replace(
            self.version,
            definition=kwargs["definition"],
            created_by=kwargs["created_by"],
        )
        return TodoTemplateDetail(self.template, (self.version,))

    def list_templates(self, **kwargs):
        self._call("list_templates", **kwargs)
        return TodoTemplatePage(total=1, items=(self.template,))

    def get_template(self, **kwargs):
        self._call("get_template", **kwargs)
        return self.template

    def list_versions(self, **kwargs):
        self._call("list_versions", **kwargs)
        return (self.version,)

    def get_version(self, **kwargs):
        self._call("get_version", **kwargs)
        return self.version

    def create_draft(self, **kwargs):
        self._call("create_draft", **kwargs)
        return self.version

    def update_draft(self, **kwargs):
        self._call("update_draft", **kwargs)
        self.version = replace(self.version, definition=kwargs["definition"])
        return self.version

    def publish_version(self, **kwargs):
        self._call("publish_version", **kwargs)
        self.version = replace(
            self.version,
            status=TodoTemplateVersionStatus.PUBLISHED,
            published_by=kwargs["published_by"],
            published_at=NOW,
        )
        return self.version

    def set_template_status(self, **kwargs):
        self._call("set_template_status", **kwargs)
        self.template = replace(self.template, status=kwargs["status"])
        return self.template


def _service(
    status: TodoTemplateStatus = TodoTemplateStatus.ACTIVE,
    *,
    role: SpaceRole = SpaceRole.ADMIN,
):
    repository = _Repository(status=status)
    access = _Access(role=role)
    return TodoTemplateService(repository, access), repository, access


def test_create_validates_definition_and_requires_space_admin_role() -> None:
    service, repository, access = _service()

    detail = service.create_template(
        space_id=7,
        actor_id="admin_1",
        definition=_definition(),
        env="pre",
    )

    assert detail.template.name == "Standard delivery"
    assert access.member_calls == [
        {"space_id": 7, "user_id": "admin_1", "env": "pre"}
    ]
    assert repository.calls == [
        {
            "operation": "create_template",
            "space_id": 7,
            "definition": _definition(),
            "created_by": "admin_1",
        }
    ]


def test_reads_require_membership_in_the_template_space() -> None:
    service, repository, access = _service()

    detail = service.get_template(template_id="todo_tpl_1", actor_id="user_1")
    version = service.get_version(
        template_id="todo_tpl_1",
        template_version_id="todo_tplv_1",
        actor_id="user_1",
    )
    page = service.list_templates(
        space_id=7,
        actor_id="user_1",
        status=None,
        page=1,
        page_size=20,
    )

    assert detail.versions == (_version(),)
    assert version == _version()
    assert page.total == 1
    assert access.member_calls == [
        {"space_id": 7, "user_id": "user_1", "env": None},
        {"space_id": 7, "user_id": "user_1", "env": None},
        {"space_id": 7, "user_id": "user_1", "env": None},
    ]
    assert [call["operation"] for call in repository.calls] == [
        "get_template",
        "list_versions",
        "get_template",
        "get_version",
        "list_templates",
    ]


def test_mutations_require_admin_of_the_addressed_template_space() -> None:
    service, repository, access = _service()

    service.update_draft(
        template_id="todo_tpl_1",
        template_version_id="todo_tplv_1",
        actor_id="admin_1",
        definition=_definition("Changed"),
    )
    service.publish_version(
        template_id="todo_tpl_1",
        template_version_id="todo_tplv_1",
        actor_id="admin_1",
    )
    service.archive_template(template_id="todo_tpl_1", actor_id="admin_1")
    service.activate_template(template_id="todo_tpl_1", actor_id="admin_1")

    assert access.member_calls == [
        {"space_id": 7, "user_id": "admin_1", "env": None},
        {"space_id": 7, "user_id": "admin_1", "env": None},
        {"space_id": 7, "user_id": "admin_1", "env": None},
        {"space_id": 7, "user_id": "admin_1", "env": None},
    ]
    assert [call["operation"] for call in repository.calls] == [
        "get_template",
        "update_draft",
        "get_template",
        "publish_version",
        "get_template",
        "set_template_status",
        "get_template",
        "set_template_status",
    ]


def test_template_mutations_reject_non_admin_space_members() -> None:
    service, repository, access = _service(role=SpaceRole.MEMBER)

    with pytest.raises(SpaceAccessDeniedError, match="administrator role required"):
        service.create_template(
            space_id=7,
            actor_id="member_1",
            definition=_definition(),
            env="pre",
        )

    assert access.member_calls == [
        {"space_id": 7, "user_id": "member_1", "env": "pre"}
    ]
    assert repository.calls == []


def test_archived_template_cannot_create_or_publish_versions() -> None:
    service, repository, _access = _service(TodoTemplateStatus.ARCHIVED)

    with pytest.raises(TodoTemplateStateConflictError, match="archived"):
        service.create_draft(template_id="todo_tpl_1", actor_id="admin_1")
    with pytest.raises(TodoTemplateStateConflictError, match="archived"):
        service.publish_version(
            template_id="todo_tpl_1",
            template_version_id="todo_tplv_1",
            actor_id="admin_1",
        )

    assert [call["operation"] for call in repository.calls] == [
        "get_template",
        "get_template",
    ]
