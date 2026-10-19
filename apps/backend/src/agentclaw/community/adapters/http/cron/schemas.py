"""Cron API Request/Response schemas.

Single source of truth for all cron HTTP contracts.
"""
from typing import Optional, Any, Dict, Union, Literal
from pydantic import BaseModel, Field, StrictStr, model_validator


class ApiResponse(BaseModel):
    """统一 API 响应格式"""
    success: bool
    message: str = "OK"
    error_code: int = 200
    data: Union[dict, list, None] = None
    failed_targets: list[dict[str, Any]] = Field(default_factory=list)


class CronListApiResponse(ApiResponse):
    """Cron list response with partial failure targets."""
    failed_targets: list[dict[str, Any]] = Field(default_factory=list)


class CreateCronRequest(BaseModel):
    """创建定时任务请求"""
    runtime_stage: Literal["draft", "verify", "online"] | None = None
    device_id: StrictStr | None = None
    device_uuid: StrictStr | None = None
    operator_id: StrictStr | None = None
    bot_id: str = Field(..., description="所属 Bot ID")
    name: str = Field(..., description="任务名称")
    schedule: str = Field(..., description="Cron 表达式")
    command: str = Field(..., description="执行的命令")
    timezone: str = Field("Asia/Shanghai", description="时区")
    enabled: bool = Field(True, description="是否启用")
    timeout_secs: int = Field(86400, description="超时时间（秒）")
    model: Optional[str] = Field(None, description="模型配置")
    notify: Optional[Dict[str, Any]] = Field(None, description="通知配置")


class UpdateCronRequest(BaseModel):
    """更新定时任务请求"""
    name: Optional[str] = Field(None, description="任务名称")
    schedule: Optional[str] = Field(None, description="Cron 表达式")
    command: Optional[str] = Field(None, description="执行的命令")
    timezone: Optional[str] = Field(None, description="时区")
    enabled: Optional[bool] = Field(None, description="是否启用")
    timeout_secs: Optional[int] = Field(None, description="超时时间（秒）")
    model: Optional[str] = Field(None, description="模型配置")
    notify: Optional[Dict[str, Any]] = Field(None, description="通知配置")


class CronBotData(BaseModel):
    """Cron 详情关联的 Bot 与运行态信息。"""

    bot_id: str
    bot_name: str
    owner_id: str
    bot_type: str
    runtime_stage: Optional[str] = None
    publish_id: Optional[int] = None
    publish_status: Optional[str] = None


class CronTaskData(BaseModel):
    """Cron 任务数据"""
    task_id: Optional[str] = None
    bot: Optional[CronBotData] = None
    name: Optional[str] = None
    schedule: Optional[str] = None
    command: Optional[str] = None
    enabled: Optional[bool] = None
    timezone: Optional[str] = None
    timeout_secs: Optional[int] = None
    model: Optional[str] = None
    notify: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CronRunData(BaseModel):
    """Cron 执行记录数据"""
    run_id: Optional[str] = None
    task_id: Optional[str] = None
    bot_id: Optional[str] = None
    bot_name: Optional[str] = None
    owner_id: Optional[str] = None
    status: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_secs: Optional[int] = None
    output: Optional[str] = None
    error: Optional[str] = None


class CronStatusData(BaseModel):
    """Cron 服务状态数据"""
    running_count: int = 0
    total_count: int = 0
    last_run_at: Optional[str] = None


class CronTargetRequest(BaseModel):
    """HTTP routing fields; engine job content retains its existing contract."""

    runtime_stage: Literal["draft", "verify", "online"] | None = None
    device_id: StrictStr | None = Field(None, min_length=1)
    device_uuid: StrictStr | None = Field(None, min_length=1)
    operator_id: StrictStr | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def validate_device_alias(self):
        if self.device_id and self.device_uuid and self.device_id != self.device_uuid:
            raise ValueError("device_id and device_uuid must match")
        self.device_id = self.device_id or self.device_uuid
        if (self.device_id or self.operator_id) and not self.runtime_stage:
            raise ValueError("targeted Cron requests require runtime_stage")
        return self
