"""BotEngineAdapter 注册表。

``BotEngineAdapterRegistry`` 按 ``engine_type`` 查对应 adapter。
openclaw / teclaw 不注册(``has()`` 返回 False),其请求在 ``BaasBotService``
内走原始分支。

具体 adapter 实现的装配由 bootstrap 完成(core 不得 import plugins)。
"""

from __future__ import annotations

from collections.abc import Mapping

from secbaas.community.logger import get_logger
from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

logger = get_logger("core-bot-run")


class BotEngineAdapterRegistry:
    """按 engine_type 查 adapter 的注册表。

    支持 eval 环境的 engine adapter：当 eval binding 的 device_props 中
    包含 engine_type 但该 engine_type 没有正式注册时，允许 fallback 到
    base adapter。
    """

    def __init__(self, adapters: Mapping[str, BotEngineAdapter]) -> None:
        self._adapters: dict[str, BotEngineAdapter] = dict(adapters)

    def get(self, engine_type: str) -> BotEngineAdapter:
        """返回已注册 adapter;未注册抛 KeyError(调用方应先 ``has()``)。"""
        try:
            return self._adapters[engine_type]
        except KeyError:
            raise KeyError(
                f"no engine adapter registered for engine_type={engine_type!r}"
            ) from None

    def has(self, engine_type: str) -> bool:
        """engine_type 是否有已注册 adapter;不抛异常。"""
        return engine_type in self._adapters

    def register_eval_support(
        self,
        engine_type: str,
        adapter: BotEngineAdapter,
    ) -> None:
        """在 registry 中注册 eval 环境的 engine adapter schema。

        当 eval binding 的 device_props 包含 engine_type 时，
        允许 registry 查找到对应的 adapter。

        Args:
            engine_type: 引擎类型
            adapter: 已初始化的 BotEngineAdapter 实例
        """
        if engine_type not in self._adapters:
            self._adapters[engine_type] = adapter
            logger.info(
                f"[BotEngineAdapterRegistry] Registered eval adapter for engine_type={engine_type!r}"
            )
