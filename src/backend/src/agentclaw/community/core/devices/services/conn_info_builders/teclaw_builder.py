"""TeclawConnInfoBuilder — 复用 baas /http-info + build_baas_conn_info_for_http(engine_type='teclaw')。

teclaw provider 标签独立(不被翻译成 baas),内部走 baas runtime 工具,
但 delivery 策略(整包 vs per-domain)由下游 TeclawDeviceSyncPlugin 区分。
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError
from agentclaw.community.core.devices.services.baas_conn_info import (
    build_baas_conn_info_for_http,
)


TECLAW_DEVICE_PROVIDER = "teclaw"


class TeclawConnInfoBuilder:
    """provider=teclaw 的 conn_info 计算器。

    底层复用 ``baas_service.get_ws_info`` + ``build_baas_conn_info_for_http(engine_type='teclaw')``,
    不重写。teclaw 与 baas 共用 transport,但 delivery 策略由下游 plugin 决定。
    """

    def __init__(self, baas_service):
        self._baas_service = baas_service

    def build(
        self,
        binding: DeviceBindingRecord,
        user_id: str,
        *,
        device_uuid: str | None = None,
    ) -> dict[str, Any]:
        try:
            ws_info = self._baas_service.get_ws_info(
                bind_id=binding.id,
                device_affinity=user_id,
                device_uuid=device_uuid,
            )
        except Exception as e:
            raise ConnInfoBuildError(
                f"TeclawConnInfoBuilder: get_ws_info failed for binding={binding.id}: {e}"
            ) from e

        # Use the *_for_http producer so conn_info carries ``bind_id``
        # (== binding.id). TeclawDeviceFileSystem reads route through
        # BaasService.invoke_http, which needs ``conn_info["bind_id"]`` exactly
        # like BaasDeviceFileSystem. Purely additive over build_baas_conn_info.
        return build_baas_conn_info_for_http(
            bind_id=binding.id,
            ws_info=ws_info,
            engine_type=TECLAW_DEVICE_PROVIDER,
            bot_type=getattr(binding, "bot_type", "") or "",
            device_provider=TECLAW_DEVICE_PROVIDER,
        )
