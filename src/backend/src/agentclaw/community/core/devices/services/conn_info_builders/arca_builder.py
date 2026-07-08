"""ArcaConnInfoBuilder — 复用 device_service.get_device_connection_v2 arca 分支。"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError


class ArcaConnInfoBuilder:
    """provider=arca 的 conn_info 计算器。

    底层委托 :meth:`DeviceService.get_device_connection_v2`(走 ARCA proxy 分支),
    不重写。本期不动 v2 内部逻辑。
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
        # arca 设备为单实例,device_uuid 仅用于多实例 BaaS provider,此处忽略。
        try:
            return self._device_service.get_device_connection_v2(
                binding_id=binding.id,
                user_id=user_id,
                nick_name=user_id,  # nick_name 在 v2 conn_info 算法中未使用,死参,传 user_id 兼容
            )
        except Exception as e:
            raise ConnInfoBuildError(
                f"ArcaConnInfoBuilder: v2 failed for binding={binding.id}: {e}"
            ) from e
