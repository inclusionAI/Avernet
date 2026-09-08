"""
Cron API Schemas — HTTP 边界模型.

只放 HTTP 请求体（frontend ↔ router）。插件层模型（CronJob / CronRunRecord /
CreateJobRequest 等）在 ``engine.community.core.cron.models`` 中定义，router 负责在两者
之间翻译（例如 HTTP 的 ``schedule: str`` 转成插件层的 ``schedule: dict``）。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class NotifyRequest(BaseModel):
    """通知配置请求"""
    enabled: bool = Field(default=False, description="是否启用通知")
    user_ids: Optional[list[str]] = Field(default=None, description="通知目标用户 ID 列表")


class EngineProperties(BaseModel):
    """引擎特有参数配置。

    每个引擎级开关都必须在此声明为显式具名字段（非开放 bag），确保契约有版本、
    有校验、随变更一并评审。当前唯一字段：

    - ``reuse_session``（aicoding agentTurn）是否复用已有会话：默认 True 复用；
      False=每次触发新开一个会话。

    消费方：corp aicoding 引擎（``corp/engines/aicoding``，仅在内部全量 checkout，
    不在 GitHub 社区版内）。社区版引擎（openclaw / claude_code）不读取本对象——
    这是显式 no-op-by-design，而非静默丢弃（详见 ``plugin_api/cron/README.md``）。
    """
    reuse_session: Optional[bool] = Field(
        default=None,
        description="（aicoding agentTurn）是否复用已有会话：默认 True 复用；False=每次触发新开会话",
    )


class CreateTaskRequest(BaseModel):
    """创建任务请求（HTTP）"""
    name: str = Field(..., description="任务名称")
    schedule: str = Field(..., description="cron表达式，如 '0 8 * * *'")
    command: str = Field(..., description="要执行的命令")
    owner_id: Optional[str] = Field(default=None, description="任务所属用户 ID")
    bot_id: Optional[str] = Field(default=None, description="任务所属 Bot ID")
    timezone: str = Field(default="Asia/Shanghai", description="时区")
    enabled: bool = Field(default=True, description="是否启用")
    timeout_secs: Optional[int] = Field(default=86400, description="任务执行超时时间（秒），默认86400")
    model: Optional[str] = Field(default=None, description="执行任务的AI模型，如gpt-4、claude-sonnet等")
    runtime: Optional[str] = Field(default=None, description="执行运行的 runtime，透传给 aicoding 创建会话时使用")
    kind: Optional[str] = Field(default=None, description="任务类型，如autoInitiate、agentTurn等，不指定时由引擎根据命令内容自动检测")
    append_message: Optional[str] = Field(default=None, description="autoInitiate任务的补充说明，执行时拼接在发起消息末尾")
    engine_properties: Optional[EngineProperties] = Field(
        default=None,
        description="引擎专属属性，仅由对应引擎处理；例如 aicoding agentTurn 的 reuse_session（是否复用已有会话）",
    )
    notify: Optional[NotifyRequest] = Field(default=None, description="通知配置")


class NotifyUpdateRequest(BaseModel):
    """通知配置更新请求（支持部分更新）"""
    enabled: Optional[bool] = None
    user_ids: Optional[list[str]] = Field(default=None, description="通知目标用户 ID 列表")


class UpdateTaskRequest(BaseModel):
    """更新任务请求（HTTP）"""
    name: Optional[str] = None
    enabled: Optional[bool] = None
    schedule: Optional[str] = None
    timezone: Optional[str] = None
    command: Optional[str] = Field(default=None, description="要执行的命令")
    timeout_secs: Optional[int] = Field(default=None, description="任务执行超时时间（秒）")
    model: Optional[str] = Field(default=None, description="执行任务的AI模型")
    runtime: Optional[str] = Field(default=None, description="执行运行的 runtime")
    engine_properties: Optional[EngineProperties] = Field(
        default=None,
        description="引擎专属属性，仅由对应引擎处理；更新时整体替换该 bag",
    )
    notify: Optional[NotifyUpdateRequest] = Field(default=None, description="通知配置（支持部分更新）")


class RunSingleAutoInitiateRequest(BaseModel):
    """单个需求/工作项发起会话请求（HTTP）。"""
    work_item_url: str = Field(..., description="需求/工作项 URL")
    user_id: str = Field(..., description="用户 ID")
    agent_id: str = Field(..., description="Agent ID")
    workflow: str = Field(default="", description="工作流名称")
    append_message: str = Field(default="", description="补充说明")
    model: Optional[str] = Field(default=None, description="模型覆盖")


__all__ = [
    "NotifyRequest",
    "EngineProperties",
    "CreateTaskRequest",
    "NotifyUpdateRequest",
    "UpdateTaskRequest",
    "RunSingleAutoInitiateRequest",
]
