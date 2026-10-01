"""Device API Converter — 领域模型 → API Response 转换.

职责：将 core 层的领域模型（DeviceBindingRecord, DeviceConnectionInfo 等）
转换为 api 层的 Pydantic Response 模型。

根据 README.md 分层规范，转换逻辑不属于 schemas.py（schemas 只定义数据结构），
也不属于 core/services（core 不感知 HTTP 契约），所以放在 api/ 层的 converter 中。
"""

import json
from typing import Any

from agentclaw.community.adapters.http.devices.schemas import (
    DeviceBindingResponse,
    DeviceBindingWithConnectionResponse,
    DeviceBindingStatus,
    DeviceConnectionResponse,
    EntityType,
    Env,
)
from agentclaw.community.core.devices.models import DeviceConnectionInfo
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


def _normalize_device_props(props: dict[str, Any]) -> dict[str, Any]:
    """处理 device_props 中的嵌套 JSON 字段，确保序列化正确。"""
    result = dict(props)
    if "symbol" in result and isinstance(result["symbol"], str):
        try:
            result["symbol"] = json.loads(result["symbol"])
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def record_to_response(record: DeviceBindingRecord | Any) -> DeviceBindingResponse:
    """将 DeviceBindingRecord 转换为 DeviceBindingResponse.

    Args:
        record: 领域层的 DeviceBindingRecord 实例

    Returns:
        API 层的 DeviceBindingResponse
    """
    if isinstance(record, DeviceBindingResponse):
        return record
    return DeviceBindingResponse(
        id=record.id,
        entity_id=record.entity_id,
        entity_type=EntityType(record.entity_type.lower()),
        device_id=record.device_id,
        device_provider=record.device_provider,
        env=Env.from_string(record.env),
        device_props=_normalize_device_props(record.device_props),
        status=DeviceBindingStatus(record.status),
        last_alive_at=record.last_alive_at,
    )


def _normalize_type(raw_type: str) -> str:
    """归一化连接类型.

    Core 层返回 tunnel/arca/baas/daas/local/desktop → Frontend 需要 local/remote/desktop.
    - type=="local" 保留为 "local"
    - type=="desktop" 保留为 "desktop"（桌面 bot 连接）
    - 其余全部映射为 "remote"

    注意: tunnel 设备在 ACTIVE 状态下由 DeviceServiceRouter 返回 type="local"
    （浏览器直连本地 adapter），OFFLINE 时返回 type="tunnel"（归一化为 remote，
    但此时 available=False，前端据此显示离线提示）。
    """
    if raw_type in ("local", "desktop"):
        return raw_type
    return "remote"


def connection_info_to_response(info: DeviceConnectionInfo) -> DeviceConnectionResponse:
    """将 DeviceConnectionInfo 转换为 DeviceConnectionResponse.

    在此边界:
    - type 归一化 (tunnel/arca → remote)
    - available / message 透传

    Args:
        info: 领域层的 DeviceConnectionInfo 实例

    Returns:
        API 层的 DeviceConnectionResponse
    """
    return DeviceConnectionResponse(
        type=_normalize_type(info.type),
        target=info.target,
        token=info.token,
        engine_type=info.engine_type,
        available=info.available,
        message=info.message,
        url=info.url,
    )


def record_to_response_with_connection(
    record: DeviceBindingRecord | Any,
    connection: DeviceConnectionInfo | None = None,
) -> DeviceBindingWithConnectionResponse:
    """将 DeviceBindingRecord + 可选连接信息转换为 DeviceBindingWithConnectionResponse.

    Args:
        record: 领域层的 DeviceBindingRecord 实例
        connection: 可选的 DeviceConnectionInfo

    Returns:
        API 层的 DeviceBindingWithConnectionResponse
    """
    base = record_to_response(record)
    conn_response = connection_info_to_response(connection) if connection else None
    return DeviceBindingWithConnectionResponse(
        **base.model_dump(),
        connection=conn_response,
    )
