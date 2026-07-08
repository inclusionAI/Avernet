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
    session_target: str = "isolated"
    state: dict[str, Any] = Field(default_factory=dict)
    notify: Optional[CronNotifyConfig] = None
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
    session_target: str = "isolated"
    enabled: bool = True
    notify: Optional[CronNotifyConfig] = None


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
