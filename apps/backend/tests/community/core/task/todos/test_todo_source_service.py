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
class _SourceClient:
    responses: dict[str, object]
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def call(self, **kwargs: Any) -> object | None:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.responses.get(kwargs["tool_name"])


def _request(*, actor_id: str | None = "149844") -> AuthRequestContext:
    return AuthRequestContext(
        cookies={"IAM_TOKEN": "iam-cookie"},
        verified_actor_user_id=actor_id,
    )


def _service(
    responses: dict[str, object],
    *,
    client: _SourceClient | None = None,
) -> tuple[TodoSourceService, _SourceClient]:
    client = client or _SourceClient(responses)
    return TodoSourceService(client), client


def _mcp_text(value: object) -> dict[str, object]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]
    }


def test_lists_projects_and_preserves_numeric_project_ids() -> None:
    service, client = _service(
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
    assert client.calls == [
        {
            "actor_id": "149844",
            "auth_request": _request(),
            "tool_name": "queryUserSpaceAndProject",
            "arguments": {},
        }
    ]


def test_valid_empty_project_payload_returns_empty_tuple() -> None:
    service, _ = _service(
        {"queryUserSpaceAndProject": _mcp_text({"data": [], "total": 0})}
    )

    assert service.list_projects(actor_id="149844", auth_request=_request()) == ()


def test_lists_and_filters_req_task_and_bug_work_items() -> None:
    service, client = _service(
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
    assert client.calls[0]["arguments"] == {"projectId": "P00000000001"}


def test_lists_workspace_work_items_with_dima_workspace_filter() -> None:
    service, client = _service(
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
    assert client.calls[0]["arguments"] == {"workspaceId": "W26001113566"}


def test_loads_editable_draft_and_strips_basic_html() -> None:
    service, client = _service(
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
    assert client.calls[0]["arguments"] == {"workItemId": "2026112000000000003"}


@pytest.mark.parametrize(
    "error",
    [
        TodoSourceAuthError("IAM token is unavailable"),
        TodoSourceResponseError("bad payload"),
        TodoSourceUnavailableError("provider failed"),
    ],
)
def test_preserves_source_client_errors(error: Exception) -> None:
    service, _ = _service({}, client=_SourceClient({}, error=error))

    with pytest.raises(type(error), match=str(error)):
        service.list_projects(actor_id="149844", auth_request=_request())


def test_normalizes_unknown_client_failures() -> None:
    service, _ = _service(
        {},
        client=_SourceClient({}, error=RuntimeError("transport failed")),
    )

    with pytest.raises(TodoSourceUnavailableError, match="Dima MCP call failed"):
        service.list_projects(actor_id="149844", auth_request=_request())


@pytest.mark.parametrize("raw", ["not json", 42])
def test_rejects_malformed_source_payload(raw: object) -> None:
    service, _ = _service({"queryUserSpaceAndProject": raw})

    with pytest.raises(TodoSourceResponseError):
        service.list_projects(actor_id="149844", auth_request=_request())


def test_missing_work_item_is_not_found() -> None:
    service, _ = _service(
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
    service, _ = _service(
        {
            "queryUserSpaceAndProject": _mcp_text(
                {"success": False, "errorMsg": "permission denied", "data": None}
            )
        }
    )

    with pytest.raises(TodoSourceUnavailableError):
        service.list_projects(actor_id="149844", auth_request=_request())
