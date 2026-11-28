from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.task.todos.bot_team_models import (
    BotTeamDefinition,
    BotTeamNode,
    BotTeamStatus,
)
from agentclaw.community.core.task.todos.errors import (
    TodoBotScopeError,
    TodoTemplateValidationError,
)
from agentclaw.community.core.task.todos.services.todo_bot_team_service import (
    TodoBotTeamProductCompositionFactory,
    TodoBotTeamService,
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

_NOW = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)


def _bot_team_definition(name: str = "Coding delivery") -> BotTeamDefinition:
    return BotTeamDefinition(
        name=name,
        description="Analyze, implement, and verify one coding Todo.",
        nodes=(
            BotTeamNode(
                code="requirement_analysis",
                name="需求分析",
                bot_ids=("bot_requirement",),
                approval_required=False,
                completion_gate="需求理解和验收项完整",
            ),
            BotTeamNode(
                code="simulator_verification",
                name="Simulator 验证",
                bot_ids=("bot_simulator", "bot_reviewer"),
                approval_required=True,
                completion_gate="Simulator 验证通过并给出结论",
            ),
        ),
    )


def _template_definition(
    name: str = "Coding delivery",
    *,
    include_unpaired_human: bool = False,
) -> TodoTemplateDefinition:
    owner = TodoParticipantReference(
        source=TodoParticipantRefSource.BINDING,
        key="todo_owner",
    )
    stages = [
        TodoStageDefinition(
            key="requirement_analysis",
            name="需求分析",
            purpose="需求分析",
            stage_kind=TodoStageKind.BOT,
            executor_ref=TodoParticipantReference(
                source=TodoParticipantRefSource.FIXED,
                subject_type=TodoParticipantSubjectType.BOT,
                subject_id="bot_requirement",
            ),
            owner_ref=owner,
            completion_gate=TodoCompletionGate(),
        ),
        TodoStageDefinition(
            key="simulator_verification",
            name="Simulator 验证",
            purpose="Simulator 验证",
            stage_kind=TodoStageKind.BOT,
            executor_ref=TodoParticipantReference(
                source=TodoParticipantRefSource.FIXED,
                subject_type=TodoParticipantSubjectType.BOT,
                subject_id="bot_simulator",
            ),
            owner_ref=owner,
            completion_gate=TodoCompletionGate(),
        ),
    ]
    if include_unpaired_human:
        stages.append(
            TodoStageDefinition(
                key="manual_gate",
                name="人工处理",
                purpose="人工处理",
                stage_kind=TodoStageKind.HUMAN,
                executor_ref=owner,
                owner_ref=owner,
                completion_gate=TodoCompletionGate(),
            )
        )
    else:
        stages.append(
            TodoStageDefinition(
                key="simulator_verification__approval",
                name="Simulator 验证审批",
                purpose="确认 Simulator 验证结果",
                stage_kind=TodoStageKind.HUMAN,
                executor_ref=owner,
                owner_ref=owner,
                completion_gate=TodoCompletionGate(),
            )
        )
    return TodoTemplateDefinition(
        schema_version="1.0",
        name=name,
        description="Analyze, implement, and verify one coding Todo.",
        binding_schema=(
            TodoParticipantDefinition(
                key="todo_owner",
                label="待办负责人",
                subject_type=TodoParticipantSubjectType.HUMAN,
                required_at=TodoParticipantRequiredAt.TODO_CREATE,
            ),
        ),
        stages=tuple(stages),
    )


def _template(
    *,
    status: TodoTemplateStatus = TodoTemplateStatus.ACTIVE,
    published_version_id: str | None = "todo_tplv_1",
) -> TodoTemplateRecord:
    return TodoTemplateRecord(
        template_id="todo_tpl_1",
        space_id=7,
        name="Coding delivery",
        description="Analyze, implement, and verify one coding Todo.",
        status=status,
        published_version_id=published_version_id,
        created_by="admin_1",
        updated_by="admin_1",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _version(
    *,
    version_no: int = 1,
    version_id: str = "todo_tplv_1",
    status: TodoTemplateVersionStatus = TodoTemplateVersionStatus.PUBLISHED,
    definition: TodoTemplateDefinition | None = None,
) -> TodoTemplateVersionRecord:
    return TodoTemplateVersionRecord(
        template_version_id=version_id,
        template_id="todo_tpl_1",
        version_no=version_no,
        status=status,
        schema_version="1.0",
        definition=definition or _template_definition(),
        definition_hash=str(version_no) * 64,
        created_by="admin_1",
        published_by="admin_1" if status is TodoTemplateVersionStatus.PUBLISHED else None,
        published_at=_NOW if status is TodoTemplateVersionStatus.PUBLISHED else None,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _Access:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def require_space_member(self, **kwargs):
        self.calls.append(kwargs)
        return (
            SimpleNamespace(
                id=kwargs["space_id"],
                space_type=SpaceType.TEAM,
                personal_owner_id=None,
                created_by="admin_1",
            ),
            SimpleNamespace(role="admin"),
        )


class _Bots:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[str] = []

    def list_live_by_bot_id(self, bot_id: str):
        self.calls.append(bot_id)
        if not self.available:
            return []
        return [
            {
                "bot_id": bot_id,
                "bot_name": f"name-{bot_id}",
                "owner_id": "bot_owner",
                "space_id": 7,
            }
        ]


class _Templates:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.template = _template(published_version_id=None)
        self.versions: list[TodoTemplateVersionRecord] = [
            _version(status=TodoTemplateVersionStatus.DRAFT)
        ]

    def _record(self, operation: str, **kwargs: Any) -> None:
        self.calls.append({"operation": operation, **kwargs})

    def create_template(self, **kwargs):
        self._record("create_template", **kwargs)
        definition = kwargs["definition"]
        self.template = replace(
            self.template,
            space_id=kwargs["space_id"],
            name=definition.name,
            description=definition.description,
            created_by=kwargs["actor_id"],
            updated_by=kwargs["actor_id"],
        )
        self.versions = [
            replace(
                _version(status=TodoTemplateVersionStatus.DRAFT),
                definition=definition,
                created_by=kwargs["actor_id"],
                published_by=None,
                published_at=None,
            )
        ]
        return TodoTemplateDetail(self.template, tuple(self.versions))

    def publish_product_version(self, **kwargs):
        self._record("publish_product_version", **kwargs)
        published = replace(
            next(
                item
                for item in self.versions
                if item.template_version_id == kwargs["template_version_id"]
            ),
            status=TodoTemplateVersionStatus.PUBLISHED,
            published_by=kwargs["actor_id"],
            published_at=_NOW,
            product_profile_id=kwargs["product_profile_id"],
        )
        self.versions = [
            published if item.template_version_id == published.template_version_id else item
            for item in self.versions
        ]
        self.template = replace(
            self.template,
            published_version_id=published.template_version_id,
            updated_by=kwargs["actor_id"],
        )
        return published

    def get_template(self, **kwargs):
        self._record("get_template", **kwargs)
        return TodoTemplateDetail(self.template, tuple(self.versions))

    def list_templates(self, **kwargs):
        self._record("list_templates", **kwargs)
        return TodoTemplatePage(total=1, items=(self.template,))

    def create_draft(self, **kwargs):
        self._record("create_draft", **kwargs)
        source = self.versions[-1]
        draft = replace(
            source,
            template_version_id="todo_tplv_2",
            version_no=2,
            status=TodoTemplateVersionStatus.DRAFT,
            published_by=None,
            published_at=None,
        )
        self.versions.append(draft)
        return draft

    def update_draft(self, **kwargs):
        self._record("update_draft", **kwargs)
        updated = replace(
            next(
                item
                for item in self.versions
                if item.template_version_id == kwargs["template_version_id"]
            ),
            definition=kwargs["definition"],
        )
        self.versions = [
            updated if item.template_version_id == updated.template_version_id else item
            for item in self.versions
        ]
        self.template = replace(
            self.template,
            name=updated.definition.name,
            description=updated.definition.description,
            updated_by=kwargs["actor_id"],
        )
        return updated

    def archive_template(self, **kwargs):
        self._record("archive_template", **kwargs)
        self.template = replace(
            self.template,
            status=TodoTemplateStatus.ARCHIVED,
            updated_by=kwargs["actor_id"],
        )
        return self.template


class _Profiles:
    def __init__(self) -> None:
        self.next_id = 100

    async def create_product_profile(self, _name, _stages):
        self.next_id += 1
        return SimpleNamespace(id=self.next_id)


def _service(*, available_bots: bool = True):
    templates = _Templates()
    access = _Access()
    bots = _Bots(available=available_bots)
    service = TodoBotTeamService(
        templates,
        access,
        bots,
        _Profiles(),
        TodoBotTeamProductCompositionFactory(),
    )
    return service, templates, access, bots


@pytest.mark.asyncio
async def test_create_bot_team_expands_nodes_and_publishes_first_revision() -> None:
    service, templates, access, bots = _service()

    detail = await service.create_bot_team(
        space_id=7,
        actor_id="admin_1",
        definition=_bot_team_definition(),
        env="pre",
    )

    assert detail.bot_team.bot_team_id == "todo_tpl_1"
    assert detail.bot_team.published_revision_id == "todo_tplv_1"
    assert detail.bot_team.status is BotTeamStatus.ACTIVE
    assert detail.revisions[0].nodes == _bot_team_definition().nodes
    assert access.calls == [
        {"space_id": 7, "user_id": "admin_1", "env": "pre"}
    ]
    assert bots.calls == ["bot_requirement", "bot_simulator", "bot_reviewer"]

    created_definition = templates.calls[0]["definition"]
    assert [stage.key for stage in created_definition.stages] == [
        "requirement_analysis",
        "simulator_verification",
        "simulator_verification__approval",
    ]
    assert created_definition.binding_schema[0].key == "todo_owner"
    assert created_definition.stages[0].executor_ref == TodoParticipantReference(
        source=TodoParticipantRefSource.FIXED,
        subject_type=TodoParticipantSubjectType.BOT,
        subject_id="bot_requirement",
    )
    assert [
        candidate.subject_id
        for candidate in created_definition.stages[1].executor_candidates
    ] == ["bot_simulator", "bot_reviewer"]
    assert (
        created_definition.stages[1].completion_condition
        == "Simulator 验证通过并给出结论"
    )
    assert [call["operation"] for call in templates.calls] == [
        "create_template",
        "publish_product_version",
        "get_template",
    ]


@pytest.mark.asyncio
async def test_update_bot_team_publishes_new_revision_without_mutating_old_revision() -> None:
    service, templates, _access, _bots = _service()
    await service.create_bot_team(
        space_id=7,
        actor_id="admin_1",
        definition=_bot_team_definition(),
    )
    first_definition = templates.versions[0].definition

    detail = await service.update_bot_team(
        bot_team_id="todo_tpl_1",
        actor_id="admin_1",
        definition=_bot_team_definition("Coding delivery v2"),
    )

    assert [revision.revision_no for revision in detail.revisions] == [1, 2]
    assert detail.bot_team.published_revision_id == "todo_tplv_2"
    assert templates.versions[0].definition == first_definition
    assert templates.versions[1].definition.name == "Coding delivery v2"
    assert [call["operation"] for call in templates.calls[-5:]] == [
        "get_template",
        "create_draft",
        "update_draft",
        "publish_product_version",
        "get_template",
    ]


@pytest.mark.asyncio
async def test_list_and_archive_keep_product_names_while_reusing_template_ids() -> None:
    service, templates, _access, _bots = _service()
    await service.create_bot_team(
        space_id=7,
        actor_id="admin_1",
        definition=_bot_team_definition(),
    )

    page = service.list_bot_teams(
        space_id=7,
        actor_id="member_1",
        status=BotTeamStatus.ACTIVE,
        page=1,
        page_size=20,
    )
    archived = service.archive_bot_team(
        bot_team_id="todo_tpl_1",
        actor_id="admin_1",
    )

    assert page.total == 1
    assert page.items[0].bot_team_id == "todo_tpl_1"
    assert page.items[0].published_revision_id == "todo_tplv_1"
    assert archived.bot_team_id == "todo_tpl_1"
    assert archived.status is BotTeamStatus.ARCHIVED
    assert templates.calls[-1]["operation"] == "archive_template"


def test_get_rejects_template_definition_that_is_not_a_bot_team_shape() -> None:
    service, templates, _access, _bots = _service()
    templates.template = _template()
    templates.versions = [
        _version(definition=_template_definition(include_unpaired_human=True))
    ]

    with pytest.raises(
        TodoTemplateValidationError,
        match="cannot be represented as a Bot team",
    ):
        service.get_bot_team(bot_team_id="todo_tpl_1", actor_id="member_1")


@pytest.mark.asyncio
async def test_create_rejects_bot_outside_space_before_persisting_template() -> None:
    service, templates, _access, _bots = _service(available_bots=False)

    with pytest.raises(TodoBotScopeError, match="not available"):
        await service.create_bot_team(
            space_id=7,
            actor_id="admin_1",
            definition=_bot_team_definition(),
        )

    assert templates.calls == []
