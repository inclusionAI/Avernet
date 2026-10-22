"""产物读侧 DTO 与翻译辅助(spec 2026-10-20-task-artifact-domain §7)。

独立小模块:``schemas.py`` 已在 1000 行帽沿,产物 Descriptor 及 record→DTO 翻译
收口于此,避免把 schemas.py 顶过 Rule 9 体积上限;仅协议翻译,无业务判断。
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field


class TaskArtifactAddRequestDTO(BaseModel):
    """新增产物请求体:与 relay 上报 item 同构的扁平自描述结构(spec §4)。

    归属字段(task_id/node_id/attempt/session_id)由核心
    ``parse_artifact_report_item`` + ``TaskArtifactApiService.add_artifact``
    与图交叉校验;四个 content 字段互斥、恰一个承载对应分支——**JSON 对象
    或 JSON 字符串均可**(独立 API 不背 relay 线上字符串包袱,调用方无须双重
    序列化;对象形态由路由层归一为字符串后进核心解析,校验语义不变)。
    """

    artifact_name: str | None = Field(
        None, description="业务展示名(可选;file 产物缺省回落 file_content.file_name)", max_length=255
    )
    artifact_kind: str = Field(
        ..., description="内容形态:text|file|url_link|code,须与非空 content 字段一致"
    )
    session_id: str | None = Field(
        None, description="协作会话 ID(图绑定优先,未绑定时采纳此值)"
    )
    task_id: str = Field(..., description="归属任务 ID")
    node_id: str = Field(..., description="产出节点 ID")
    attempt: int = Field(..., ge=0, description="归属 attempt(须与图上 harness_retries 快照一致)")
    text_content: dict | str | None = Field(
        None, description="text 分支内容对象(或 JSON 字符串,下同),如 {\"text\": \"...\"};四个 content 字段互斥"
    )
    file_content: dict | str | None = Field(
        None,
        description="file 分支内容对象:storage_type(bot_workspace|session_file)/storage_id"
        "(bot_workspace=工作区相对路径,拒绝绝对路径与 .. 逃逸)/file_type/file_name/file_id(session_file 必填)",
    )
    url_link_content: dict | str | None = Field(
        None, description="url_link 分支内容对象:url(http s)+type(业务类型)"
    )
    code_content: dict | str | None = Field(
        None, description="code 分支内容对象:code_base_name/code_base_address/pr_link/pr_desc"
    )

    model_config = {"extra": "forbid"}


class TaskArtifactDescriptorDTO(BaseModel):
    """产物读侧 Descriptor:只含安全字段(spec §7)。

    content 为 kind-tagged 引用元数据 wire 形(File 分支 storage_type/storage_id/
    file_name/file_id/file_type;UrlLink 分支 url/type;Code 分支四字段)——不含任何
    临时下载凭证/字节 handle,前端自行基于既有读口拼链接。
    """

    artifact_id: str = Field(..., description="产物ID")
    artifact_name: str | None = Field(
        None, description="业务展示名(file 产物回落 file_name;未提供为 null)"
    )
    kind: str = Field(..., description="内容形态:text|file|url_link|code")
    content: dict = Field(
        default_factory=dict, description="kind-tagged 引用元数据(JSON object)"
    )
    attempt: int | None = Field(None, description="归属 attempt(harness_retries 快照)")
    creator: str | None = Field(None, description="产出者(bot_id/group_id;system=系统写入)")
    created_at: int = Field(..., description="发布时间(ms epoch)")


def artifact_record_to_descriptor(record) -> TaskArtifactDescriptorDTO:
    """TaskArtifactRecord → Descriptor DTO(§7 安全字段;仅协议翻译,无业务判断)。"""

    from agentclaw.community.core.task.task_context.task_artifact import content_to_json

    return TaskArtifactDescriptorDTO(
        artifact_id=record.artifact_id,
        artifact_name=record.artifact_name,
        kind=record.kind,
        content=json.loads(content_to_json(record.content)),
        attempt=record.attempt,
        creator=record.creator,
        created_at=record.created_at,
    )


def group_artifacts_by_node(records) -> dict[str, list]:
    """读侧产物按 node 分组(仓储已给出 attempt/gmt_create 倒序,只分组不重排)。"""

    mapping: dict[str, list] = {}
    for record in records:
        mapping.setdefault(record.node_id, []).append(record)
    return mapping