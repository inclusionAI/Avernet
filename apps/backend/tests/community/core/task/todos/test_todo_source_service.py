from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from agentclaw.community.core.task.todos.errors import (
    TodoSourceAuthError,
    TodoSourceResponseError,
    TodoSourceUnavailableError,
    TodoSourceWorkItemNotFoundError,
)
from agentclaw.community.core.task.todos.services.todo_source_service import (
    TodoSourceService,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext


@dataclass
class _Auth:
    token: str | None = "access-token"
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def exchange_iam_token(self, subject_token: str) -> str | None:
        self.calls.append(subject_token)
        if self.error:
            raise self.error
        return self.token


@dataclass
class _Market:
    detail: object = field(
        default_factory=lambda: {
            "serverCode": "mcp.ant.arkai.dimamcpserver",
            "endpoints": [],
        }
    )
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def get_mcp_detail(self, server_code: str) -> object:
        self.calls.append(server_code)
        if self.error:
            raise self.error
        return self.detail


@dataclass
class _ToolCalls:
    responses: dict[str, object]
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def call_tool(self, **kwargs: Any) -> object | None:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.responses.get(kwargs["tool_name"])


def _request(
    *,
    actor_id: str | None = "149844",
    cookies: dict[str, str] | None = None,
) -> AuthRequestContext:
    return AuthRequestContext(
        cookies=cookies if cookies is not None else {"IAM_TOKEN": "iam-cookie"},
        verified_actor_user_id=actor_id,
    )


def _service(
    responses: dict[str, object],
    *,
    auth: _Auth | None = None,
    market: _Market | None = None,
    calls: _ToolCalls | None = None,
) -> tuple[TodoSourceService, _Auth, _Market, _ToolCalls]:
    auth = auth or _Auth()
    market = market or _Market()
    calls = calls or _ToolCalls(responses)
    return TodoSourceService(auth, market, calls), auth, market, calls


def _mcp_text(value: object) -> dict[str, object]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]
    }


def test_lists_projects_and_preserves_numeric_project_ids() -> None:
    service, auth, market, calls = _service(
        {
            "queryUserSpaceAndProject": _mcp_text(
                {
                    "data": {
                        "projectInfo": [
                            {"id": 1259, "projectName": "团队项目"},
                            {
                                "projectId": "P00000000001",
                                "projectName": "个人项目",
                            },
                        ],
                        "workspaceInfo": [
                            {
                                "workspaceId": "W00000000001",
                                "workspaceName": "团队空间",
                            }
                        ],
                    }
                }
            )
        }
    )

    projects = service.list_projects(actor_id="149844", auth_request=_request())

    assert [(item.id, item.name) for item in projects] == [
        ("P00000000001", "个人项目"),
        ("W00000000001", "团队空间"),
        ("1259", "团队项目"),
    ]
    assert auth.calls == ["iam-cookie"]
    assert market.calls == ["mcp.ant.arkai.dimamcpserver"]
    assert calls.calls[0]["tool_name"] == "queryUserSpaceAndProject"
    assert calls.calls[0]["arguments"] == {}
    assert calls.calls[0]["access_token"] == "access-token"


def test_valid_empty_project_payload_returns_empty_tuple() -> None:
    service, *_ = _service(
        {"queryUserSpaceAndProject": _mcp_text({"data": [], "total": 0})}
    )

    assert service.list_projects(actor_id="149844", auth_request=_request()) == ()


def test_lists_and_filters_req_task_and_bug_work_items() -> None:
    service, *_, calls = _service(
        {
            "queryUserWorkItemList": _mcp_text(
                {
                    "data": [
                        {
                            "workitemId": "2026112000000000001",
                            "subject": "支付需求",
                            "workitemCategory": "Req",
                            "statusName": "处理中",
                        },
                        {
                            "workItemId": "2026112000000000002",
                            "subject": "回归任务",
                            "templateTypeName": "Task",
                        },
                        {
                            "workItemId": "2026112000000000003",
                            "subject": "登录缺陷",
                            "templateTypeName": "Bug",
                        },
                    ]
                }
            )
        }
    )

    items = service.list_work_items(
        actor_id="149844",
        auth_request=_request(),
        project_id="P00000000001",
        query="支付",
    )

    assert [(item.id, item.category, item.title) for item in items] == [
        ("2026112000000000001", "req", "支付需求")
    ]
    assert calls.calls[0]["arguments"] == {"projectId": "P00000000001"}


def test_lists_workspace_work_items_with_dima_workspace_filter() -> None:
    service, *_, calls = _service(
        {"queryUserWorkItemList": _mcp_text({"data": {"reqList": []}})}
    )

    assert (
        service.list_work_items(
            actor_id="149844",
            auth_request=_request(),
            project_id="W26001113566",
        )
        == ()
    )
    assert calls.calls[0]["arguments"] == {"workspaceId": "W26001113566"}


def test_loads_editable_draft_and_strips_basic_html() -> None:
    service, _auth, _market, calls = _service(
        {
            "queryWorkItemDetail": _mcp_text(
                {
                    "workitemId": "2026112000000000003",
                    "subject": "登录缺陷",
                    "workitemCategory": "Bug",
                    "statusName": "处理中",
                    "content": "<p>第一行&nbsp;</p><div>第二行</div>",
                    "url": "https://dima.example/work-item/3",
                    "updatedAt": "2026-11-20T12:00:00Z",
                }
            )
        }
    )

    draft = service.load_work_item_draft(
        actor_id="149844",
        auth_request=_request(),
        project_id="P00000000001",
        work_item_id="2026112000000000003",
    )

    assert draft.category == "bug"
    assert draft.title == "登录缺陷"
    assert draft.description == (
        "来源：Dima BUG 2026112000000000003\n"
        "项目：P00000000001\n"
        "状态：处理中\n\n"
        "第一行\n第二行"
    )
    assert draft.source_url == "https://dima.example/work-item/3"
    assert draft.source_updated_at == "2026-11-20T12:00:00Z"
    assert calls.calls[0]["arguments"] == {
        "workItemId": "2026112000000000003"
    }


@pytest.mark.parametrize(
    ("auth_request", "auth", "expected"),
    [
        (_request(cookies={}), _Auth(), TodoSourceAuthError),
        (_request(actor_id="other"), _Auth(), TodoSourceAuthError),
        (_request(), _Auth(token=None), TodoSourceAuthError),
        (_request(), _Auth(error=RuntimeError("exchange failed")), TodoSourceAuthError),
    ],
)
def test_rejects_unusable_human_identity(
    auth_request: AuthRequestContext,
    auth: _Auth,
    expected: type[Exception],
) -> None:
    service, *_ = _service({}, auth=auth)

    with pytest.raises(expected):
        service.list_projects(actor_id="149844", auth_request=auth_request)


@pytest.mark.parametrize(
    ("market", "calls"),
    [
        (_Market(detail=None), _ToolCalls({})),
        (_Market(error=RuntimeError("metadata failed")), _ToolCalls({})),
        (_Market(), _ToolCalls({}, error=RuntimeError("transport failed"))),
        (_Market(), _ToolCalls({"queryUserSpaceAndProject": None})),
    ],
)
def test_normalizes_provider_failures(
    market: _Market,
    calls: _ToolCalls,
) -> None:
    service, *_ = _service({}, market=market, calls=calls)

    with pytest.raises(TodoSourceUnavailableError):
        service.list_projects(actor_id="149844", auth_request=_request())


@pytest.mark.parametrize("raw", ["not json", 42])
def test_rejects_malformed_source_payload(raw: object) -> None:
    service, *_ = _service({"queryUserSpaceAndProject": raw})

    with pytest.raises(TodoSourceResponseError):
        service.list_projects(actor_id="149844", auth_request=_request())


def test_missing_work_item_is_not_found() -> None:
    service, *_ = _service(
        {"queryWorkItemDetail": _mcp_text({"data": {"id": "different"}})}
    )

    with pytest.raises(TodoSourceWorkItemNotFoundError):
        service.load_work_item_draft(
            actor_id="149844",
            auth_request=_request(),
            project_id="P00000000001",
            work_item_id="2026112000000000003",
        )


def test_rejects_dima_business_error_response() -> None:
    service, *_ = _service(
        {
            "queryUserSpaceAndProject": _mcp_text(
                {"success": False, "errorMsg": "permission denied", "data": None}
            )
        }
    )

    with pytest.raises(TodoSourceUnavailableError):
        service.list_projects(actor_id="149844", auth_request=_request())
