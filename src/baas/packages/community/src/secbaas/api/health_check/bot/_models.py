"""
Bot Health Checker 数据模型

用于 BotHealthCheckerService 的请求和响应模型。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from secbaas.api.health_check.paas import PaasHealthCheckerResult


class DeviceAliveStatus(StrEnum):
    """设备活跃状态枚举"""

    LIVE = "live"
    IDLE = "idle"
    UNKNOWN = "unknown"
    ERROR = "error"


# ============ Configuration ============


class BotHealthCheckerConfig(BaseModel):
    """Bot Health Checker 配置"""

    health_check_timeout: int = Field(
        default=10, ge=1, description="健康检查超时时间（秒）"
    )
    health_check_max_concurrent: int = Field(
        default=10, ge=1, description="最大并发健康检查数"
    )
    extend_when_remaining_hours: int = Field(
        default=16, ge=1, description="剩余时间 ≤ 16 小时时续期"
    )
    target_ttl_hours: int = Field(default=24, ge=1, description="目标 TTL（小时）")


# ============ 健康检查策略 ============


# 默认检查器配置：按 provider_type
DEFAULT_HEALTH_CHECKERS: dict[str, list[str]] = {
    "ARCA": ["engine", "adapter", "gateway"],
    "POOLAB": ["api"],
    "SIGMA": [],  # Sigma 暂未实现
    "LOCAL": [],  # 本地设备无检查器
    "K8S": ["readiness"],
}

# 按 active_engine 的特殊检查器配置
ENGINE_HEALTH_CHECKERS: dict[str, dict[str, list[str]]] = {
    # active_engine -> provider_type -> checkers
    "openclaw": {"ARCA": ["engine", "adapter", "gateway"], "K8S": ["readiness"]},
    "aicoding": {"ARCA": ["echo_aicoding"], "K8S": ["readiness"]},
    "claude_code": {"ARCA": ["echo_claude_code"], "K8S": ["readiness"]},
}

# 未配置引擎时的兜底检查器配置：按 provider_type
DEFAULT_ENGINE_FALLBACK_CHECKERS: dict[str, list[str]] = {
    "ARCA": ["echo"],
    "POOLAB": ["api"],
    "K8S": ["readiness"],
}


def resolve_health_check_strategy(
    provider_type: str | None,
    active_engine: str | None = None,
) -> list[str]:
    """解析健康检查策略。

    1. 优先按 active_engine 查找 ENGINE_HEALTH_CHECKERS
    2. 引擎未配置，或引擎配置中不包含此 provider_type 时，查找 DEFAULT_ENGINE_FALLBACK_CHECKERS
    3. 回退到 DEFAULT_HEALTH_CHECKERS
    4. 未配置则返回空列表

    Args:
        provider_type: 平台类型 (ARCA/SIGMA/local)
        active_engine: 当前激活的引擎

    Returns:
        检查器列表
    """
    if provider_type is None:
        return []

    provider_type_upper = provider_type.upper()

    # 1. 优先按 active_engine 查找
    if active_engine and active_engine in ENGINE_HEALTH_CHECKERS:
        engine_config = ENGINE_HEALTH_CHECKERS[active_engine]
        if provider_type_upper in engine_config:
            return engine_config[provider_type_upper]

    # 2. 未配置引擎时查找兜底配置
    if provider_type_upper in DEFAULT_ENGINE_FALLBACK_CHECKERS:
        return DEFAULT_ENGINE_FALLBACK_CHECKERS[provider_type_upper]

    # 3. 回退到默认配置
    return DEFAULT_HEALTH_CHECKERS.get(provider_type_upper, [])


# ============ 活跃检查策略 ============


# 按 active_engine 的活跃检查器配置
ENGINE_ALIVE_CHECKERS: dict[str, dict[str, list[str]]] = {
    # active_engine -> provider_type -> checkers
    "openclaw": {"ARCA": ["active"], "K8S": ["liveness"]},
    "hermes": {"ARCA": ["active_hermes"], "K8S": ["liveness"]},
    "aicoding": {"ARCA": ["active_claude_code"], "K8S": ["liveness"]},
    "claude_code": {"ARCA": ["active_claude_code"], "K8S": ["liveness"]},
}

# 未配置引擎时的默认活跃检查器配置：按 provider_type
DEFAULT_ALIVE_CHECKERS: dict[str, list[str]] = {"K8S": ["liveness"]}


def resolve_alive_check_strategy(
    provider_type: str | None,
    active_engine: str | None = None,
) -> list[str]:
    """解析活跃检查策略。

    1. 优先按 active_engine 查找 ENGINE_ALIVE_CHECKERS
    2. 未配置引擎时查找 DEFAULT_ALIVE_CHECKERS
    3. 未配置则返回空列表（表示不支持 alive 检查）

    Args:
        provider_type: 平台类型 (ARCA/SIGMA/local)
        active_engine: 当前激活的引擎

    Returns:
        检查器列表，空列表表示不支持 alive 检查
    """
    if provider_type is None:
        return []

    provider_type_upper = provider_type.upper()

    # 1. 优先按 active_engine 查找
    if active_engine and active_engine in ENGINE_ALIVE_CHECKERS:
        engine_config = ENGINE_ALIVE_CHECKERS[active_engine]
        if provider_type_upper in engine_config:
            return engine_config[provider_type_upper]

    # 2. 未配置引擎时查找默认配置
    if provider_type_upper in DEFAULT_ALIVE_CHECKERS:
        return DEFAULT_ALIVE_CHECKERS[provider_type_upper]

    # 3. 未配置则返回空列表（不支持）
    return []


# ============ Response Models ============


class BotDeviceListResponse(BaseModel):
    """Bot 设备列表分页响应"""

    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页")
    page_size: int = Field(..., description="每页数量")
    items: list["BotDeviceInfo"] = Field(default_factory=list, description="Bot 列表")


class BotDeviceInfo(BaseModel):
    """Bot 设备信息（list_all_active_bot_device 返回）"""

    model_config = ConfigDict(populate_by_name=True)

    bot_id: str = Field(..., description="Bot ID")
    entity_id: str = Field(..., description="实体 ID")
    binding_id: int | None = Field(
        default=None, description="ac_entity_device_binding.id（未绑定时为 None）"
    )
    bot_type: str = Field(..., description="Bot 类型: personal | service")
    status: str = Field(..., description="Bot 状态")
    active_engine: str | None = Field(default=None, description="当前激活的引擎")

    @classmethod
    def from_binding_dict(cls, binding: dict) -> "BotDeviceInfo":
        """从 Repository 查询结果字典构造 BotDeviceInfo。

        Args:
            binding: Repository 返回的绑定信息字典

        Returns:
            BotDeviceInfo 实例
        """
        binding_id = binding.get("binding_id")
        return cls(
            bot_id=binding["bot_id"],
            entity_id=binding["entity_id"],
            binding_id=int(binding_id) if binding_id is not None else None,
            bot_type=binding.get("bot_type"),
            status=binding["status"],
            active_engine=binding.get("active_engine"),
        )


class PaasDeviceListResponse(BaseModel):
    """Bot 的设备列表响应（list_paas_device_by_bot 返回）"""

    model_config = ConfigDict(populate_by_name=True)

    bot_id: str = Field(..., description="Bot ID")
    entity_id: str = Field(..., description="实体 ID")
    bot_type: str = Field(..., description="Bot 类型: personal | service")
    active_engine: str | None = Field(
        default=None, description="Bot 级别，用于健康检查策略确定"
    )
    paas_devices: list["PaasDeviceInfo"] = Field(
        default_factory=list, description="PaaS 设备列表"
    )


class PaasDeviceInfo(BaseModel):
    """PaaS 设备信息"""

    model_config = ConfigDict(populate_by_name=True)

    paas_device_id: str = Field(..., description="设备 ID，格式: ARCA-SANDBOX-xxx@0")
    device_uuid: str | None = Field(
        default=None, description="设备 UUID (service 类型)"
    )
    provider_type: str | None = Field(
        default=None, description="设备级别: ARCA | SIGMA | local"
    )
    status: str = Field(default="UNKNOWN", description="设备状态")
    query_status: str | None = Field(
        default=None,
        description="查询来源状态（仅 service 类型: draft/validating/online）",
    )
    ttl_expiration_time: str | None = Field(
        default=None, description="TTL 过期时间字符串"
    )
    ttl_expiration_timestamp: int | None = Field(
        default=None, description="TTL 过期时间戳（毫秒）"
    )
    source_table: str | None = Field(
        default=None, description="数据来源表: ac_binding | baas_device"
    )
    source_table_id: str | None = Field(
        default=None, description="来源表主键: binding_id 或 device_uuid"
    )
    refresh_fail_count: int = Field(default=0, description="TTL 刷新连续失败次数")


@dataclass(slots=True)
class TTLInfo:
    """TTL 续期操作结果"""

    paas_device_id: str
    old_expiration_time: datetime | None
    new_expiration_time: datetime | None
    success: bool = False  # 续期是否成功
    skipped: bool = False  # 是否跳过（剩余时间充足）
    error: str | None = None  # 错误信息


class TTLExtendResult(BaseModel):
    """TTL 续期结果"""

    model_config = ConfigDict(populate_by_name=True)

    bot_id: str = Field(..., description="Bot ID")
    bot_type: str = Field(..., description="Bot 类型 (personal/service)")
    total_devices: int = Field(..., description="设备总数")
    extended_count: int = Field(default=0, description="成功续期数量")
    skipped_count: int = Field(default=0, description="跳过续期数量（剩余时间充足）")
    failed_count: int = Field(default=0, description="失败数量")
    details: list["TTLInfo"] = Field(default_factory=list, description="各设备续期详情")
    error: str | None = Field(default=None, description="整体错误信息")


class FailedDeviceInfo(BaseModel):
    """失败设备信息"""

    model_config = ConfigDict(populate_by_name=True)

    paas_device_id: str = Field(..., description="设备 ID")
    error_message: str | None = Field(default=None, description="错误信息")
    failed_checkers: list[str] | None = Field(
        default=None, description="失败的检查器列表（健康检查场景）"
    )


class BotHealthCheckResult(BaseModel):
    """Bot 级别健康检查结果（check_health_by_bot 返回）"""

    model_config = ConfigDict(populate_by_name=True)

    bot_id: str = Field(..., description="Bot ID")
    entity_id: str = Field(..., description="实体 ID")
    bot_type: str = Field(..., description="Bot 类型 (personal/service)")
    active_engine: str | None = Field(default=None, description="当前激活的引擎")
    overall_healthy: bool = Field(..., description="所有设备健康才为 True")
    healthy_count: int = Field(default=0, description="健康设备数")
    unhealthy_count: int = Field(default=0, description="不健康设备数")
    devices: list[PaasHealthCheckerResult] = Field(
        default_factory=list, description="设备检查结果列表"
    )
    failed_devices: list["FailedDeviceInfo"] = Field(
        default_factory=list, description="失败设备详情"
    )


class BotAliveCheckResult(BaseModel):
    """Bot 级别活跃检查结果（check_alive_by_bot 返回）"""

    model_config = ConfigDict(populate_by_name=True)

    bot_id: str = Field(..., description="Bot ID")
    entity_id: str = Field(..., description="实体 ID")
    bot_type: str = Field(..., description="Bot 类型 (personal/service)")
    active_engine: str | None = Field(default=None, description="当前激活的引擎")
    minutes: int = Field(..., description="检查时间范围（分钟）")
    overall_alive: bool | None = Field(
        ..., description="所有设备 live 为 True，全 idle 为 False，其余为 None"
    )
    live_count: int = Field(default=0, description="live 设备数")
    idle_count: int = Field(default=0, description="idle 设备数")
    unknown_count: int = Field(default=0, description="unknown 设备数")
    error_count: int = Field(default=0, description="error 设备数")
    devices: list["AliveDeviceInfo"] = Field(
        default_factory=list, description="设备活跃检查结果列表"
    )


class AliveDeviceInfo(BaseModel):
    """单个设备活跃检查结果"""

    model_config = ConfigDict(populate_by_name=True)

    paas_device_id: str = Field(..., description="设备 ID")
    status: DeviceAliveStatus = Field(
        ..., description="设备活跃状态: live/idle/unknown/error"
    )
    last_session_time: str | None = Field(
        default=None, description="最近会话时间（格式: YYYY-MM-DD HH:MM:SS）"
    )
    error: str | None = Field(default=None, description="错误信息")
