"""Device core models — 业务内部模型.

These models represent the domain concepts for device management
and are used within the core layer. They mirror the original models
from services/device/models.py but are self-contained in the new
architecture.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


@dataclass
class AllocatedDevice:
    """Allocated device information.

    Represents a device that has been successfully allocated.
    """
    device_id: str
    device_provider: str
    device_props: dict[str, Any]


class EntityType(StrEnum):
    """Entity type enumeration."""
    STAFF = "staff"
    TEAM = "team"
    PROJ = "proj"


class Env(StrEnum):
    """Environment enumeration."""
    DEV = "dev"
    PRE = "pre"
    PROD = "prod"

    @classmethod
    def from_string(cls, value: str) -> "Env":
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(
                f"Invalid env value: {value!r}. Expected one of: dev, pre, prod"
            ) from None


class DeviceBindingStatus(StrEnum):
    """Device binding status enumeration."""
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    RELEASED = "RELEASED"


class DeviceAllocationMode(StrEnum):
    """Device allocation mode enumeration."""
    SINGLE = "single"  # Single device per entity
    MULTI = "multi"    # Multiple devices allowed


@dataclass
class OperatorContext:
    """Operator context for device operations.

    Contains information about the user performing the operation.
    """
    staff_id: str
    staff: str
    nick_name: str
    operator_name: str
    tenant_id: str | None = None


# =============================================================================
# Domain Value Objects — service 层返回这些类型，而非 API Response
# =============================================================================

@dataclass
class NasMappingInfo:
    """NAS mount mapping — 领域值对象."""
    nas_remote: str
    nas_local: str
    nas_param: str | None = None
    nas_permission: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nas_remote": self.nas_remote,
            "nas_local": self.nas_local,
            "nas_param": self.nas_param,
            "nas_permission": self.nas_permission,
        }


@dataclass
class SynlinkMappingInfo:
    """Symbolic link mapping — 领域值对象."""
    source: str
    target: str
    skill_uuid: str | None = None  # 仅 center:// skill 设置
    version: str | None = None     # 仅 center:// skill 设置

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "skill_uuid": self.skill_uuid,
            "version": self.version,
        }


@dataclass
class DeviceConnectionInfo:
    """设备连接信息 — 领域值对象.

    service 层返回此类型，由 converter 转为 DeviceConnectionResponse.
    """
    type: str
    target: str
    token: str
    engine_type: str
    available: bool = True       # 设备是否可达
    message: str = ""            # 不可达时的说明
    # BaaS bot 级 invoke-http 所需字段（仅 baas/desktop 类型填充）
    baas_base_url: str = ""
    bot_uuid: str = ""
    tenant: str = ""
    engine_port: int = 20003
    url: str = ""
    """连接 URL；随模式返回 HTTP base URL 或 WebSocket URL。

    LocalDeviceService 通过 BaaS get_http_info 填充 HTTP URL；BaaS relay
    WebSocket 链路透传 ws-info 返回的 ws_url。BaaS 非 relay 模式保持为空。
    expert_chat 等 3 处 caller 已用 `conn.get("url") or f"http://{conn['target']}"`
    模式优先取 url，保持现有调用方兼容。
    """
    expires_at: str = ""
    """``token`` 的过期时间（ISO 8601），由签发方给出；未知时为空串。

    只在该值确实描述本对象返回的那个 ``token`` 时填充：BaaS 链路返回
    ws-info 的 token，因此透传 ws-info 的 expires_at；LocalDeviceService
    正常路径返回的是 http-info 的 token（BaaS 未给过期时间），只有在
    http-info 失败回落到 ws-info token 时才填。空串表示"签发方没说"，
    caller 不得据此推断"永不过期"。
    """
    ws_target: str = ""
    """WebSocket 路由目标；未单独提供时 caller 回退到 ``target``。

    LocalDeviceService 的 HTTP-info 与 WS-info 会签发不同的 target/token
    组合，因此必须像凭据一样分别携带，不能把 HTTP target 与 WS token
    拼在一起。
    """
    ws_token: str = ""
    """WebSocket 凭据 —— 开 socket 的 caller 用这个，不要用 ``token``.

    ``token`` 是 *HTTP* 侧的凭据：LocalDeviceService 正常路径返回的是
    http-info 的 token，WebSocket 则使用 ``ws_target/ws_token`` 这一对
    （见下方 ``ws_expires_at``）。

    只在它与 ``token`` **不同**时填：LocalDeviceService 正常路径填 ws-info 的
    token；BaaS 链路的 ``token`` 本就是 ws token，留空。空串的含义是"``token``
    就是它"，caller 一律 ``ws_token or token``。
    """
    ws_expires_at: str = ""
    """``ws_token`` 的过期时间（ISO 8601）；未知时为空串。

    与 ``expires_at`` 分开是因为它们描述的是两个 token：http-info 不给
    过期时间，ws-info 给，二者不能互相顶替。
    """


@dataclass
class DeviceBindingInfo:
    """设备绑定信息 — 领域值对象.

    service 层返回此类型（或直接返回 DeviceBindingRecord），
    由 router/converter 转为 DeviceBindingResponse。
    直接复用 DeviceBindingRecord 即可，此类用于需要附加信息的场景。
    """
    record: Any  # DeviceBindingRecord
    connection: DeviceConnectionInfo | None = None


@dataclass
class DeviceBindingContext:
    """Plugin 侧的 per-bot binding 快照（plan-01 新增）.

    供 LocalDeviceFileSystem / LocalDeviceSyncPlugin / LocalDeviceMCPSyncPlugin /
    LocalHealthProbe 这 4 个 plan-02~04 改造的 plugin 构造时使用。它把"调用
    BaaS get_http_info 所需要的全部上下文"打包成一个不可变快照，避免每个 plugin
    各自再次穿透 DeviceBindingRepository 查 binding。

    构造方：`DeviceFilesystemDispatcher.for_bot(bot_id, user_id)` 在选定 local
    provider 后从 conn_info / binding repo 中提取这几个字段；详见 spec §3.2。
    """

    binding_id: int
    """ac_entity_device_binding.id —— 传给 BaaS get_http_info 的 bind_id。"""

    device_id: str
    """bot_uuid（与 binding.device_id 同源）—— 仅日志/诊断使用。"""

    entity_id: str
    """user / team / proj 标识 —— 传给 BaaS get_http_info 的 device_affinity。"""

    adapter_port: int = 20003
    """容器 adapter 端口。dev 固定 20003，local standalone 动态 20010-20099；plan-02
    起的 plugin 业务调用会以此为 port 参数。"""

    tenant: str = ""
    """租户名 —— BaaS 端用于路由；空时由 BaasService 走 self._tenant 默认。"""
