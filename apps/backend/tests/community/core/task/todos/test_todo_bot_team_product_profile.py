from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
    current_actor_context,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmStagesPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
    MethodTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.stages_yaml import (
    compile_stages,
)
from agentclaw.community.core.task.todos.bot_team_models import (
    BotTeamDefinition,
    BotTeamNode,
)
from agentclaw.community.core.task.todos.services.todo_bot_team_service import (
    TodoBotTeamService,
    build_bot_team_product_profile_spec,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateDetail,
    TodoTemplateRecord,
    TodoTemplateStatus,
    TodoTemplateVersionRecord,
    TodoTemplateVersionStatus,
)

_NOW = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
_COMPOSITION = MethodCompositionTemplate(
    (
        MethodTemplate(
            get_profile("Swarm"),
            SwarmStagesPolicy(
                "openapi-bot/v1",
                "trusted-evidence/v1",
                "stages",
            ),
        ),
    )
)


def _definition() -> BotTeamDefinition:
    return BotTeamDefinition(
        name="Coding delivery",
        description="Analyze and implement.",
        nodes=(
            BotTeamNode(
                code="requirement_analysis",
                name="需求分析",
                bot_ids=("bot-requirement",),
                approval_required=False,
                completion_gate="需求理解和验收项完整",
            ),
            BotTeamNode(
                code="code_development",
                name="代码开发",
                bot_ids=("bot-developer", "bot-reviewer"),
                approval_required=True,
                completion_gate="代码已提交并通过验证",
            ),
        ),
    )


def test_product_profile_spec_is_deterministic_and_uses_only_original_bot_nodes() -> None:
    first = build_bot_team_product_profile_spec(_definition(), _COMPOSITION)
    second = build_bot_team_product_profile_spec(_definition(), _COMPOSITION)

    assert first == second
    assert first.name == "Coding delivery"
    assert first.composition == _COMPOSITION
    compiled = compile_stages(first.stages)
    assert [stage.name for stage in compiled] == [
        "requirement_analysis",
        "code_development",
    ]
    assert compiled[0].depends_on == ()
    assert compiled[1].depends_on == ("requirement_analysis",)
    assert [candidate.agent_id for candidate in compiled[1].candidates] == [
        "bot-developer",
        "bot-reviewer",
    ]
    assert compiled[1].conditions == ("代码已提交并通过验证",)
    assert "__approval" not in first.stages


class _CompositionFactory:
    def build(self) -> MethodCompositionTemplate:
        return _COMPOSITION


class _Profiles:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create_product_profile(self, name, stages):
        self.calls.append(
            {
                "name": name,
                "stages": stages,
                "context": current_actor_context(),
            }
        )
        if self.error is not None:
            raise self.error
        return ProductProfile(101, name, _COMPOSITION, stages)


class _Templates:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.definition = None
        self.version = None
        self.template = TodoTemplateRecord(
            template_id="todo_tpl_1",
            space_id=7,
            name="Coding delivery",
            description="Analyze and implement.",
            status=TodoTemplateStatus.ACTIVE,
            published_version_id=None,
            created_by="admin-1",
            updated_by="admin-1",
            created_at=_NOW,
            updated_at=_NOW,
        )

    def create_template(self, **kwargs):
        self.calls.append({"operation": "create_template", **kwargs})
        self.definition = kwargs["definition"]
        self.version = TodoTemplateVersionRecord(
            template_version_id="todo_tplv_1",
            template_id="todo_tpl_1",
            version_no=1,
            status=TodoTemplateVersionStatus.DRAFT,
            schema_version="1.0",
            definition=self.definition,
            definition_hash="a" * 64,
            created_by="admin-1",
            published_by=None,
            published_at=None,
            created_at=_NOW,
            updated_at=_NOW,
            product_profile_id=None,
        )
        return TodoTemplateDetail(self.template, (self.version,))

    def publish_product_version(self, **kwargs):
        self.calls.append({"operation": "publish_product_version", **kwargs})
        self.version = replace(
            self.version,
            status=TodoTemplateVersionStatus.PUBLISHED,
            product_profile_id=kwargs["product_profile_id"],
            published_by=kwargs["actor_id"],
            published_at=_NOW,
        )
        self.template = replace(
            self.template,
            published_version_id=self.version.template_version_id,
        )
        return self.version

    def get_template(self, **kwargs):
        self.calls.append({"operation": "get_template", **kwargs})
        return TodoTemplateDetail(self.template, (self.version,))


class _Access:
    def require_space_member(self, **kwargs):
        return (
            SimpleNamespace(
                id=kwargs["space_id"],
                space_type=SpaceType.TEAM,
                personal_owner_id=None,
                created_by="admin-1",
            ),
            SimpleNamespace(role="admin"),
        )


class _Bots:
    def list_live_by_bot_id(self, bot_id: str):
        return [
            {
                "bot_id": bot_id,
                "bot_name": bot_id,
                "owner_id": "owner",
                "space_id": 7,
            }
        ]


@pytest.mark.asyncio
async def test_create_publishes_profile_before_revision(monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "pre")
    profiles = _Profiles()
    templates = _Templates()
    service = TodoBotTeamService(
        templates,
        _Access(),
        _Bots(),
        profiles,
        _CompositionFactory(),
    )

    detail = await service.create_bot_team(
        space_id=7,
        actor_id="admin-1",
        definition=_definition(),
    )

    assert profiles.calls == [{
        "name": "Coding delivery",
        "stages": build_bot_team_product_profile_spec(_definition(), _COMPOSITION).stages,
        "context": ActorContext(
        PrincipalRef("human", "admin-1"),
        "todo_product",
        ),
    }]
    assert [call["operation"] for call in templates.calls] == [
        "create_template",
        "publish_product_version",
        "get_template",
    ]
    assert detail.revisions[0].product_profile_id == 101


@pytest.mark.asyncio
async def test_profile_failure_keeps_revision_unpublished(monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "pre")
    profiles = _Profiles(error=RuntimeError("profile unavailable"))
    templates = _Templates()
    service = TodoBotTeamService(
        templates,
        _Access(),
        _Bots(),
        profiles,
        _CompositionFactory(),
    )

    with pytest.raises(RuntimeError, match="profile unavailable"):
        await service.create_bot_team(
            space_id=7,
            actor_id="admin-1",
            definition=_definition(),
        )

    assert templates.template.published_version_id is None
    assert [call["operation"] for call in templates.calls] == ["create_template"]
