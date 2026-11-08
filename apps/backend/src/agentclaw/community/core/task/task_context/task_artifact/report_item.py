"""relay 回调上报产物 item 解析:扁平自描述结构(spec 2026-10-20-task-artifact-domain §4)。

结构性校验全部收口在此(HTTP 层转既有 4xx 错误信封;写侧按既有报文异常出口处理):
四个 content 字段**恰好一个**非空且与 ``artifact_kind`` 一致;content JSON **不含 kind tag**,
判别信息由字段名承载;``task_id``/``node_id`` 必须等于本报文级定位;``attempt`` 须为 ≥0 整数;
列表上限 64,超限整批拒绝——产物记录宁可没有,不可有假。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskArtifactContentError
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
    ArtifactContent,
)

_MAX_REPORT_ITEMS = 64

_KIND_TO_FIELD: dict[ArtifactKind, str] = {
    ArtifactKind.TEXT: "text_content",
    ArtifactKind.FILE: "file_content",
    ArtifactKind.URL_LINK: "url_link_content",
    ArtifactKind.CODE: "code_content",
}


@dataclass(frozen=True)
class ArtifactReportItem:
    """一条上报产物:归属字段 + 恰好一个已构造的内容分支对象。

    ``session_id`` 交叉校验(图绑定优先、上报兜底)发生在发布点(Task 6 capture),
    解析层只接受 str | None。``artifact_patch`` 以 ``list[ArtifactReportItem]``
    挂上 ``TaskNodePatch``(None=未上报;[]=明确空)。
    """

    artifact_name: str | None
    artifact_kind: str
    session_id: str | None
    task_id: str
    node_id: str
    attempt: int
    content: ArtifactContent


def parse_artifact_report_item(
    raw: dict[str, Any],
    *,
    report_task_id: str,
    report_node_id: str,
) -> ArtifactReportItem:
    """解析并校验一条上报 item;任一规则失配 ``raise ValueError``,不猜分支。"""

    if not isinstance(raw, dict):
        raise ValueError("artifact report item must be a JSON object")
    raw_kind = raw.get("artifact_kind")
    try:
        kind = ArtifactKind(raw_kind)
    except ValueError as exc:
        raise ValueError(f"artifact_kind 非法: {raw_kind!r}") from exc

    non_empty_fields = [
        field for field in _KIND_TO_FIELD.values() if raw.get(field) is not None
    ]
    if len(non_empty_fields) != 1:
        raise ValueError(
            "artifact item 要求恰好一个 content 字段非空, 四个 content 字段互斥; "
            f"得到: {non_empty_fields or '无'}"
        )
    expected_field = _KIND_TO_FIELD[kind]
    if non_empty_fields[0] != expected_field:
        raise ValueError(
            f"artifact_kind {kind.value} 与非空 content 字段 {non_empty_fields[0]} 不一致"
        )

    payload = raw[expected_field]
    if isinstance(payload, dict):
        # 对象形态(2026-10-22 用户定稿,线上契约):字段即对象,直通分支构造器。
        fields = payload
    elif isinstance(payload, str):
        # 字符串形态(兼容旧约/手动 API 同构容忍):单行 JSON 字符串。
        try:
            fields = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{expected_field} 不是合法 JSON: {exc}") from exc
        if not isinstance(fields, dict):
            raise ValueError(f"{expected_field} 必须是 JSON object")
    else:
        raise ValueError(f"{expected_field} 必须是 JSON object 或 JSON 字符串")
    # content JSON 不含 kind tag(§4 规则 2)——判别由字段名承载,tag 由序列化器注入。
    if "kind" in fields:
        raise ValueError(f"{expected_field} 不应包含 kind tag")
    try:
        content = _build_branch(kind, fields)
    except TaskArtifactContentError as exc:
        raise ValueError(f"{expected_field} 分支校验失败: {exc}") from exc

    task_id = raw.get("task_id")
    node_id = raw.get("node_id")
    if task_id != report_task_id:
        raise ValueError(f"artifact item task_id {task_id!r} 与报文级 {report_task_id!r} 不符")
    if node_id != report_node_id:
        raise ValueError(f"artifact item node_id {node_id!r} 与报文级 {report_node_id!r} 不符")

    attempt = raw.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        raise ValueError(f"artifact item attempt 须为 ≥0 整数,得到: {attempt!r}")

    session_id = raw.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise ValueError("artifact item session_id 须为字符串或 null")

    artifact_name = raw.get("artifact_name")
    if artifact_name is not None:
        if not isinstance(artifact_name, str) or not artifact_name.strip():
            raise ValueError("artifact item artifact_name 须为非空字符串或 null")
        if len(artifact_name) > 255:
            raise ValueError("artifact item artifact_name 超长(>255)")

    return ArtifactReportItem(
        artifact_name=artifact_name,
        artifact_kind=kind.value,
        session_id=session_id,
        task_id=task_id,
        node_id=node_id,
        attempt=attempt,
        content=content,
    )


def parse_artifact_report_items(
    items: list,
    *,
    report_task_id: str,
    report_node_id: str,
) -> list[ArtifactReportItem]:
    """列表级包装:超 64 条整批拒绝;空列表=明确空(区别于未上报)。"""

    if items is None:
        raise ValueError("artifacts 列表不可为 null(省略=未上报)")
    if len(items) > _MAX_REPORT_ITEMS:
        raise ValueError(
            f"artifacts 上报条数 {len(items)} 超过上限 {_MAX_REPORT_ITEMS}, 整批拒绝"
        )
    return [
        parse_artifact_report_item(
            item, report_task_id=report_task_id, report_node_id=report_node_id
        )
        for item in items
    ]


def _build_branch(kind: ArtifactKind, fields: dict[str, Any]) -> ArtifactContent:
    """按 kind 注入 tag 后走 ``content_from_json``——分支构造器校验/枚举 coercion
    单点复用(未知字段/缺字段/互斥破坏/路径逃逸全部由其 fail-loud)。"""

    from agentclaw.community.core.task.task_context.task_artifact.models import (
        content_from_json,
    )

    tagged = json.dumps({"kind": kind.value, **fields}, ensure_ascii=False)
    return content_from_json(tagged)