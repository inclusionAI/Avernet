"""LocalConnInfoBuilder — singlebox/local provider 的 conn_info 计算器。

底层复用 ``device_service.get_device_connection_v2`` 的 local/desktop 分支
(它会走 BaaS invoke-http 代理)。
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError


class LocalConnInfoBuilder:
    """provider=local 的 conn_info 计算器。

    singlebox/local 环境下 v2 走 desktop/local 分支,内部已通过 BaaS
    invoke-http 代理拨号。本 builder 不重写算法。
    """

    def __init__(self, device_service):
        self._device_service = device_service

    def build(
        self,
        binding: DeviceBindingRecord,
        user_id: str,
        *,
        device_uuid: str | None = None,
    ) -> dict[str, Any]:
        # 本地设备为单实例,device_uuid 仅用于多实例 BaaS provider,此处忽略。
        try:
            return self._device_service.get_device_connection_v2(
                binding_id=binding.id,
                user_id=user_id,
                nick_name=user_id,  # nick_name 是死参,传 user_id 兼容
            )
        except Exception as e:
            raise ConnInfoBuildError(
                f"LocalConnInfoBuilder: v2 failed for binding={binding.id}: {e}"
            ) from e
