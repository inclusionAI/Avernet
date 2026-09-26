"""BotService 选择器 — 根据 binding_info 选择 BotService 实现。"""

from __future__ import annotations

from secbaas.community.api.bot_runtime import BotBindingInfo

from ._bot_run_utils import BAAS_DEVICE_PROVIDERS, CALLER_DEVICE_PROVIDER
from ._internal_protocols import BotService


class BotServiceSelector:
    """根据 device_provider 选择 BotService 实现。"""

    def __init__(
        self,
        claw_service: BotService,
        baas_service: BotService,
        caller_service: BotService | None = None,
    ):
        self._claw_service = claw_service
        self._baas_service = baas_service
        self._caller_service = caller_service

    def select(self, binding_info: BotBindingInfo | None) -> BotService:
        if binding_info and binding_info.device_provider == CALLER_DEVICE_PROVIDER:
            if self._caller_service is None:
                # 未装配 CallerBotService 时退回 claw（保持旧行为，不静默选错设备）。
                return self._claw_service
            return self._caller_service
        if binding_info and binding_info.device_provider in BAAS_DEVICE_PROVIDERS:
            return self._baas_service
        return self._claw_service
