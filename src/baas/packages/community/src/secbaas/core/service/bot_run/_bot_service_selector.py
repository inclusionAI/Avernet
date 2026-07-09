"""BotService 选择器 — 根据 binding_info 选择 BotService 实现。"""

from __future__ import annotations

from secbaas.api.bot_runtime import BotBindingInfo

from ._internal_protocols import BotService

# 使用 BaasBotService 的 device_provider 集合
# 注：引擎差异（aicoding/hermes/claude_code）已下沉到 BotEngineAdapter SPI，
# 在 BaasBotService 内按 registry.has(engine_type) 分流；新增引擎不扩展本集合。
_BAAS_PROVIDERS = frozenset({"baas", "teclaw"})


class BotServiceSelector:
    """根据 device_provider 选择 BotService 实现。"""

    def __init__(self, claw_service: BotService, baas_service: BotService):
        self._claw_service = claw_service
        self._baas_service = baas_service

    def select(self, binding_info: BotBindingInfo | None) -> BotService:
        if binding_info and binding_info.device_provider in _BAAS_PROVIDERS:
            return self._baas_service
        return self._claw_service
