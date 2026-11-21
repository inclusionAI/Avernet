"""Import Dima work items into normalized, editable Todo drafts."""

from __future__ import annotations

import html
import json
import re
from typing import Any, Iterable

from injector import inject

from agentclaw.community.core.mcp.mcp_auth_service_protocol import (
    MCPAuthServiceProtocol,
)
from agentclaw.community.core.mcp.mcp_market_service_protocol import (
    MCPMarketServiceProtocol,
)
from agentclaw.community.core.mcp.mcp_tool_call_service_protocol import (
    MCPToolCallServiceProtocol,
)
from agentclaw.community.core.task.todos.errors import (
    TodoSourceAuthError,
    TodoSourceResponseError,
    TodoSourceUnavailableError,
    TodoSourceWorkItemNotFoundError,
)
from agentclaw.community.core.task.todos.models import (
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
)
from agentclaw.community.core.task.todos.source_models import (
    TodoSourceDraft,
    TodoSourceProject,
    TodoSourceWorkItem,
    TodoSourceWorkItemKind,
)
from agentclaw.community.core.task.todos.source_service_protocol import (
    TodoSourceServiceProtocol,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext

_DIMA_SERVER_CODE = "mcp.ant.arkai.dimamcpserver"
_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"<(?:br\s*/?|/p|/div|/li)>|<(?:p|div|li)(?:\s[^>]*)?>", re.I)
_SPACE_RE = re.compile(r"[ \t]+")


class TodoSourceService(TodoSourceServiceProtocol):
    """Use the authenticated Human's Dima MCP identity for read-only imports."""

    @inject
    def __init__(
        self,
        auth: MCPAuthServiceProtocol,
        market: MCPMarketServiceProtocol,
        tool_calls: MCPToolCallServiceProtocol,
    ) -> None:
        self._auth = auth
        self._market = market
        self._tool_calls = tool_calls

    def list_projects(
        self,
        *,
        actor_id: str,
        auth_request: AuthRequestContext,
    ) -> tuple[TodoSourceProject, ...]:
        payload = self._call(
            actor_id=actor_id,
            auth_request=auth_request,
            tool_name="queryUserSpaceAndProject",
            parameter={},
        )
        projects = _normalize_projects(payload)
        if not projects and not isinstance(payload, (dict, list)):
            raise TodoSourceResponseError("Dima project response is not recognizable")
        return projects

    def list_work_items(
        self,
        *,
        actor_id: str,
        auth_request: AuthRequestContext,
        project_id: str,
        query: str | None = None,
    ) -> tuple[TodoSourceWorkItem, ...]:
        payload = self._call(
            actor_id=actor_id,
            auth_request=auth_request,
            tool_name="queryUserWorkItemList",
            parameter={
                "workspaceId" if project_id.upper().startswith("W") else "projectId": (
                    project_id
                )
            },
        )
        items = _normalize_work_items(payload, project_id=project_id)
        normalized_query = (query or "").strip().casefold()
        if normalized_query:
            items = tuple(
                item
                for item in items
                if normalized_query in item.title.casefold()
                or normalized_query in item.id.casefold()
            )
        if not items and not isinstance(payload, (dict, list)):
            raise TodoSourceResponseError("Dima work-item response is not recognizable")
        return items

    def load_work_item_draft(
        self,
        *,
        actor_id: str,
        auth_request: AuthRequestContext,
        project_id: str,
        work_item_id: str,
    ) -> TodoSourceDraft:
        payload = self._call(
            actor_id=actor_id,
            auth_request=auth_request,
            tool_name="queryWorkItemDetail",
            parameter={"workItemId": work_item_id},
        )
        detail = _select_work_item_detail(payload, work_item_id)
        if detail is None:
            raise TodoSourceWorkItemNotFoundError(work_item_id)
        return _draft_from_detail(
            detail,
            project_id=project_id,
            work_item_id=work_item_id,
        )

    def _call(
        self,
        *,
        actor_id: str,
        auth_request: AuthRequestContext,
        tool_name: str,
        parameter: dict[str, object],
    ) -> object:
        if auth_request.verified_actor_user_id not in (None, actor_id):
            raise TodoSourceAuthError("verified actor mismatch")
        iam_token = auth_request.cookies.get("IAM_TOKEN") or auth_request.cookies.get(
            "iam_token"
        )
        if not iam_token:
            raise TodoSourceAuthError("IAM token is unavailable")
        try:
            access_token = self._auth.exchange_iam_token(iam_token)
        except Exception as exc:  # noqa: BLE001 -- provider failures are normalized
            raise TodoSourceAuthError("IAM token exchange failed") from exc
        if not access_token:
            raise TodoSourceAuthError("IAM token exchange failed")
        try:
            detail = self._market.get_mcp_detail(_DIMA_SERVER_CODE)
        except Exception as exc:  # noqa: BLE001 -- upstream is a deployment concern
            raise TodoSourceUnavailableError(
                "Dima MCP metadata is unavailable"
            ) from exc
        if not isinstance(detail, dict):
            raise TodoSourceUnavailableError("Dima MCP metadata is unavailable")
        try:
            raw = self._tool_calls.call_tool(
                server_detail=detail,
                tool_name=tool_name,
                arguments=parameter,
                access_token=access_token,
            )
        except Exception as exc:  # noqa: BLE001 -- transport errors are normalized
            raise TodoSourceUnavailableError("Dima MCP call failed") from exc
        if raw is None:
            raise TodoSourceUnavailableError("Dima MCP call failed")
        return _decode_tool_result(raw)


def _decode_tool_result(raw: object) -> object:
    current = raw
    for _ in range(4):
        if isinstance(current, dict):
            if current.get("isError") is True or current.get("success") is False:
                raise TodoSourceUnavailableError("Dima MCP tool returned an error")
            content = current.get("content")
            if isinstance(content, list):
                texts = [
                    item.get("text")
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                ]
                if texts:
                    current = "\n".join(texts)
                    continue
            for key in ("data", "result"):
                if key in current and len(current) == 1:
                    current = current[key]
                    break
            else:
                return current
            continue
        if isinstance(current, str):
            text = current.strip()
            if not text:
                return {}
            try:
                current = json.loads(text)
            except json.JSONDecodeError as exc:
                raise TodoSourceResponseError(
                    "Dima MCP returned non-JSON text"
                ) from exc
            continue
        return current
    return current


def _walk_dicts(value: object) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_dicts(nested)


def _text(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _normalize_projects(payload: object) -> tuple[TodoSourceProject, ...]:
    projects: dict[str, TodoSourceProject] = {}
    for record in _walk_dicts(payload):
        project_id = _text(
            record,
            "projectId",
            "project_id",
            "projectCode",
            "project_code",
            "workspaceId",
            "workspace_id",
        )
        name = _text(
            record,
            "projectName",
            "project_name",
            "projectTitle",
            "project_title",
            "workspaceName",
            "workspace_name",
            "name",
            "subject",
            "title",
        )
        generic_id = _text(record, "id")
        has_project_shape = any(
            record.get(key) not in (None, "")
            for key in (
                "projectName",
                "project_name",
                "projectTitle",
                "project_title",
                "workspaceName",
                "workspace_name",
            )
        )
        if (
            project_id is None
            and generic_id
            and (has_project_shape or generic_id.upper().startswith(("P", "W")))
        ):
            project_id = generic_id
        if project_id is None:
            continue
        if name:
            projects[project_id] = TodoSourceProject(id=project_id, name=name)
    return tuple(sorted(projects.values(), key=lambda item: item.name.casefold()))


def _normalize_kind(value: str | None) -> TodoSourceWorkItemKind:
    normalized = (value or "").casefold()
    if "bug" in normalized or "缺陷" in normalized:
        return "bug"
    if "req" in normalized or "需求" in normalized:
        return "req"
    return "task"


def _work_item_from_record(
    record: dict[str, Any],
    *,
    project_id: str,
) -> TodoSourceWorkItem | None:
    item_id = _text(
        record,
        "workItemId",
        "workitemId",
        "work_item_id",
        "itemId",
        "item_id",
    )
    generic_id = _text(record, "id")
    has_work_item_shape = any(
        record.get(key) not in (None, "")
        for key in (
            "workItemTypeName",
            "work_item_type_name",
            "templateTypeName",
            "template_type_name",
            "objectTypeName",
            "object_type_name",
            "workitemCategory",
        )
    )
    if item_id is None and generic_id and (generic_id.isdigit() or has_work_item_shape):
        item_id = generic_id
    title = _text(record, "subject", "title", "name", "workItemName")
    if item_id is None or title is None:
        return None
    category = _normalize_kind(
        _text(
            record,
            "workItemTypeName",
            "work_item_type_name",
            "templateTypeName",
            "template_type_name",
            "objectTypeName",
            "object_type_name",
            "workitemCategory",
            "type",
            "category",
        )
    )
    return TodoSourceWorkItem(
        id=item_id,
        project_id=(
            _text(record, "projectId", "project_id", "workspaceId", "workspace_id")
            or project_id
        ),
        category=category,
        title=title[:MAX_TITLE_LENGTH],
        status=_text(record, "statusName", "status_name", "status"),
        updated_at=_text(
            record,
            "updatedAt",
            "updated_at",
            "gmtModified",
            "gmt_modified",
            "modifiedAt",
        ),
    )


def _normalize_work_items(
    payload: object,
    *,
    project_id: str,
) -> tuple[TodoSourceWorkItem, ...]:
    items: dict[str, TodoSourceWorkItem] = {}
    for record in _walk_dicts(payload):
        item = _work_item_from_record(record, project_id=project_id)
        if item is not None:
            items[item.id] = item
    return tuple(items.values())


def _select_work_item_detail(
    payload: object,
    work_item_id: str,
) -> dict[str, Any] | None:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for record in _walk_dicts(payload):
        record_id = _text(
            record,
            "workItemId",
            "workitemId",
            "work_item_id",
            "itemId",
            "item_id",
            "id",
        )
        if record_id != work_item_id:
            continue
        score = sum(
            1
            for key in (
                "subject",
                "title",
                "content",
                "description",
                "statusName",
                "templateTypeName",
                "url",
            )
            if record.get(key) not in (None, "")
        )
        candidates.append((score, record))
    if not candidates:
        root = payload if isinstance(payload, dict) else None
        if root and _text(root, "subject", "title", "name"):
            return root
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _plain_text(value: object) -> str:
    if isinstance(value, str):
        text = _BREAK_RE.sub("\n", value)
        text = _TAG_RE.sub("", text)
        text = html.unescape(text)
        lines = [_SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value).strip() if value is not None else ""


def _draft_from_detail(
    detail: dict[str, Any],
    *,
    project_id: str,
    work_item_id: str,
) -> TodoSourceDraft:
    item = _work_item_from_record(detail, project_id=project_id)
    title = _text(detail, "subject", "title", "name", "workItemName")
    if not title:
        raise TodoSourceResponseError("Dima work-item title is missing")
    category = (
        item.category
        if item
        else _normalize_kind(
            _text(
                detail,
                "templateTypeName",
                "workItemTypeName",
                "workitemCategory",
                "type",
                "category",
            )
        )
    )
    status = (
        item.status if item else _text(detail, "statusName", "status_name", "status")
    )
    content = ""
    for key in (
        "content",
        "description",
        "detail",
        "workItemContent",
        "work_item_content",
        "body",
    ):
        if detail.get(key) not in (None, ""):
            content = _plain_text(detail[key])
            if content:
                break
    metadata = [f"来源：Dima {category.upper()} {work_item_id}", f"项目：{project_id}"]
    if status:
        metadata.append(f"状态：{status}")
    description = "\n".join(metadata)
    if content:
        description = f"{description}\n\n{content}"
    return TodoSourceDraft(
        source_type="dima",
        project_id=project_id,
        work_item_id=work_item_id,
        category=category,
        title=title[:MAX_TITLE_LENGTH],
        description=description[:MAX_DESCRIPTION_LENGTH],
        source_url=_text(detail, "url", "webUrl", "web_url", "detailUrl"),
        source_updated_at=(
            item.updated_at
            if item
            else _text(detail, "updatedAt", "updated_at", "gmtModified")
        ),
    )
