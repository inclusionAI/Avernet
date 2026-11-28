from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import Request, Response
from pydantic import ValidationError

from agentclaw.community.adapters.http.openapi_v1.task.todos.bot_team_router import (
    archive_bot_team,
    create_bot_team,
    get_bot_team,
    list_bot_teams,
    router,
    update_bot_team,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.bot_team_schemas import (
    CreateBotTeamRequest,
    UpdateBotTeamRequest,
)
from agentclaw.community.core.task.todos.bot_team_models import (
    BotTeamDefinition,
    BotTeamDetail,
    BotTeamNode,
    BotTeamPage,
    BotTeamRecord,
    BotTeamRevisionRecord,
    BotTeamStatus,
)
_NOW = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-bot-team"
    return request


class _PageParams:
    def __init__(self, *, page: int, page_size: int) -> None:
        self.page = page
        self.page_size = page_size


def _definition(name: str = "Coding team") -> BotTeamDefinition:
    return BotTeamDefinition(
        name=name,
        description="Analyze, implement, and verify the Todo.",
        nodes=(
            BotTeamNode(
                code="develop",
                name="代码开发",
                bot_ids=("bot_developer", "bot_reviewer"),
                approval_required=True,
                completion_gate="代码已提交并通过验证",
            ),
        ),
    )


def _payload(name: str = "Coding team") -> dict[str, Any]:
    return {
        "name": name,
        "description": "Analyze, implement, and verify the Todo.",
        "nodes": [
            {
                "code": "develop",
                "name": "代码开发",
                "bot_ids": ["bot_developer", "bot_reviewer"],
                "approval_required": True,
                "completion_gate": "代码已提交并通过验证",
            }
        ],
    }


def _record(*, status: BotTeamStatus = BotTeamStatus.ACTIVE) -> BotTeamRecord:
    return BotTeamRecord(
        bot_team_id="todo_tpl_1",
        space_id=7,
        name="Coding team",
        description="Analyze, implement, and verify the Todo.",
        status=status,
        published_revision_id="todo_tplv_1",
        created_by="admin_1",
        updated_by="admin_1",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _revision() -> BotTeamRevisionRecord:
    definition = _definition()
    return BotTeamRevisionRecord(
        bot_team_revision_id="todo_tplv_1",
        bot_team_id="todo_tpl_1",
        revision_no=1,
        name=definition.name,
        description=definition.description,
        nodes=definition.nodes,
        definition_hash="a" * 64,
        product_profile_id=101,
        created_by="admin_1",
        published_by="admin_1",
        published_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _detail() -> BotTeamDetail:
    return BotTeamDetail(bot_team=_record(), revisions=(_revision(),))


class _RecordedService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _record(self, operation: str, **kwargs: Any) -> None:
        self.calls.append({"operation": operation, **kwargs})

    async def create_bot_team(self, **kwargs):
        self._record("create_bot_team", **kwargs)
        return _detail()

    def list_bot_teams(self, **kwargs):
        self._record("list_bot_teams", **kwargs)
        return BotTeamPage(total=1, items=(_record(),))

    def get_bot_team(self, **kwargs):
        self._record("get_bot_team", **kwargs)
        return _detail()

    async def update_bot_team(self, **kwargs):
        self._record("update_bot_team", **kwargs)
        return _detail()

    def archive_bot_team(self, **kwargs):
        self._record("archive_bot_team", **kwargs)
        return _record(status=BotTeamStatus.ARCHIVED)


@pytest.mark.asyncio
async def test_create_bot_team_returns_product_contract_and_delegates() -> None:
    service = _RecordedService()
    response = Response()

    result = await create_bot_team(
        body=CreateBotTeamRequest(space_id=7, **_payload()),
        request=_request("POST", "/openapi/v1/collaboration/tasks/bot-teams"),
        response=response,
        user_id="admin_1",
        service=service,
    )

    assert response.status_code == 201
    assert result.code == 201000
    assert result.request_id == "trace-bot-team"
    assert result.data.bot_team.bot_team_id == "todo_tpl_1"
    assert result.data.revisions[0].nodes[0].bot_ids == [
        "bot_developer",
        "bot_reviewer",
    ]
    assert result.data.revisions[0].nodes[0].completion_gate == "代码已提交并通过验证"
    assert "template_id" not in result.data.model_dump()
    assert service.calls == [
        {
            "operation": "create_bot_team",
            "space_id": 7,
            "actor_id": "admin_1",
            "definition": _definition(),
        }
    ]


@pytest.mark.asyncio
async def test_list_get_update_and_archive_delegate_exact_product_inputs() -> None:
    service = _RecordedService()

    page = await list_bot_teams(
        request=_request("GET", "/openapi/v1/collaboration/tasks/bot-teams"),
        page_params=_PageParams(page=2, page_size=25),
        user_id="member_1",
        space_id=7,
        status=BotTeamStatus.ACTIVE,
        service=service,
    )
    detail = await get_bot_team(
        bot_team_id="todo_tpl_1",
        request=_request(
            "GET", "/openapi/v1/collaboration/tasks/bot-teams/todo_tpl_1"
        ),
        user_id="member_1",
        service=service,
    )
    updated = await update_bot_team(
        bot_team_id="todo_tpl_1",
        body=UpdateBotTeamRequest(**_payload("Updated coding team")),
        request=_request(
            "PUT", "/openapi/v1/collaboration/tasks/bot-teams/todo_tpl_1"
        ),
        user_id="admin_1",
        service=service,
    )
    archived = await archive_bot_team(
        bot_team_id="todo_tpl_1",
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/bot-teams/todo_tpl_1/archive"
        ),
        user_id="admin_1",
        service=service,
    )

    assert page.data.total == 1
    assert page.data.items[0].status is BotTeamStatus.ACTIVE
    assert detail.data.revisions[0].revision_no == 1
    assert updated.data.bot_team.name == "Coding team"
    assert archived.data.status is BotTeamStatus.ARCHIVED
    assert service.calls == [
        {
            "operation": "list_bot_teams",
            "space_id": 7,
            "actor_id": "member_1",
            "status": BotTeamStatus.ACTIVE,
            "page": 2,
            "page_size": 25,
        },
        {
            "operation": "get_bot_team",
            "bot_team_id": "todo_tpl_1",
            "actor_id": "member_1",
        },
        {
            "operation": "update_bot_team",
            "bot_team_id": "todo_tpl_1",
            "actor_id": "admin_1",
            "definition": _definition("Updated coding team"),
        },
        {
            "operation": "archive_bot_team",
            "bot_team_id": "todo_tpl_1",
            "actor_id": "admin_1",
        },
    ]


def test_bot_team_requests_are_strict_and_require_nodes() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CreateBotTeamRequest.model_validate(
            {"space_id": 7, **_payload(), "template_id": "do-not-expose"}
        )

    payload = _payload()
    payload["nodes"][0]["template_stage_key"] = "do-not-expose"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        UpdateBotTeamRequest.model_validate(payload)

    with pytest.raises(ValidationError, match="too_short"):
        CreateBotTeamRequest(
            space_id=7,
            name="Empty team",
            description="",
            nodes=[],
        )


def test_bot_team_routes_are_static_todos_siblings() -> None:
    operations = {
        (method, route.path)
        for route in router.routes
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}
    }

    assert operations == {
        ("GET", "/openapi/v1/collaboration/tasks/bot-teams"),
        ("POST", "/openapi/v1/collaboration/tasks/bot-teams"),
        ("GET", "/openapi/v1/collaboration/tasks/bot-teams/{bot_team_id}"),
        ("PUT", "/openapi/v1/collaboration/tasks/bot-teams/{bot_team_id}"),
        (
            "POST",
            "/openapi/v1/collaboration/tasks/bot-teams/{bot_team_id}/archive",
        ),
    }
