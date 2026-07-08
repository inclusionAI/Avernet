"""PaaS Health Checker 数据模型。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthCheckerStrategyResult(BaseModel):
    """单个健康检查器的检查结果"""

    model_config = ConfigDict(populate_by_name=True)

    healthy: bool = Field(..., description="是否健康")
    response: dict[str, Any] | None = Field(default=None, description="响应数据")
    error: str | None = Field(default=None, description="错误信息")
    timeout: bool = Field(default=False, description="是否超时")
    duration_ms: int = Field(..., description="执行耗时（毫秒）")


class PaasHealthCheckerResult(BaseModel):
    """单个设备的健康检查结果（可能包含多个检查器）"""

    model_config = ConfigDict(populate_by_name=True)

    paas_device_id: str = Field(..., description="设备 ID")
    overall_healthy: bool = Field(..., description="任一组件失败则为 False")
    checkers: dict[str, HealthCheckerStrategyResult] = Field(
        default_factory=dict, description="检查器名称 -> 结果"
    )
    query_status: str | None = Field(
        default=None,
        description="查询来源状态（仅 service 类型: draft/validating/online）",
    )
    source_table: str | None = Field(
        default=None, description="数据来源表: ac_binding | baas_device"
    )
    source_table_id: str | None = Field(
        default=None, description="来源表主键: binding_id 或 device_uuid"
    )
