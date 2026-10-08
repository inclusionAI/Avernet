"""Cron DTOs shared across core adapters and profile plugins."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CronNotifyConfig(BaseModel):
    """任务通知配置"""
    enabled: bool = True
    user_ids: Optional[list[str]] = None


class CronJob(BaseModel):
    """定时任务模型"""
    id: str
    name: str
    enabled: bool = True
    schedule: dict[str, Any]
    payload: dict[str, Any]
    owner_id: Optional[str] = Field(default=None, description="创建/拥有该 cron 任务的 owner 标识")
    bot_id: Optional[str] = Field(default=None, description="与该 cron 任务关联的 bot 标识")
    session_target: str = "isolated"
    state: dict[str, Any] = Field(default_factory=dict)
    notify: Optional[CronNotifyConfig] = None
    # 引擎专属属性 bag：仅由对应引擎消费，其它引擎原样忽略（透传时也忽略）。
    # 例如 aicoding agentTurn 任务的 ``reuse_session``（是否复用已有会话）。
    engine_properties: Optional[dict[str, Any]] = Field(
        default=None,
        description="引擎专属属性，仅由对应引擎处理；例如 aicoding 的 reuse_session",
    )
    created_at_ms: int
    updated_at_ms: int


class CronRunRecord(BaseModel):
    """任务执行记录"""
    job_id: str
    started_at_ms: int
    finished_at_ms: int
    status: str
    error: Optional[str] = None
    duration_ms: int
    output: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class CronStatus(BaseModel):
    """Cron 服务状态"""
    running: bool
    job_count: int
    enabled_count: int
    next_run_at_ms: Optional[int] = None


class CreateJobRequest(BaseModel):
    """创建任务请求（内部 API）"""
    name: str
    schedule: dict[str, Any]
    payload: dict[str, Any]
    owner_id: Optional[str] = Field(default=None, description="创建/拥有该 cron 任务的 owner 标识")
    bot_id: Optional[str] = Field(default=None, description="与该 cron 任务关联的 bot 标识")
    session_target: str = "isolated"
    enabled: bool = True
    notify: Optional[CronNotifyConfig] = None
    engine_properties: Optional[dict[str, Any]] = Field(
        default=None,
        description="引擎专属属性，仅由对应引擎处理；例如 aicoding 的 reuse_session",
    )


class CronNotifyPatch(BaseModel):
    """通知配置部分更新（支持单独更新 enabled 或 user_ids）"""
    enabled: Optional[bool] = None
    user_ids: Optional[list[str]] = None


class UpdateJobRequest(BaseModel):
    """更新任务请求（内部 API）"""
    name: Optional[str] = None
    schedule: Optional[dict[str, Any]] = None
    payload: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None
    notify: Optional[CronNotifyPatch] = None
    engine_properties: Optional[dict[str, Any]] = Field(
        default=None,
        description="引擎专属属性，仅由对应引擎处理；整体替换该 bag（例如 aicoding 的 reuse_session）",
    )
