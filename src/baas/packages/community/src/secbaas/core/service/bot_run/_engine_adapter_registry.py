"""BotEngineAdapter 注册表。

``BotEngineAdapterRegistry`` 按 ``engine_type`` 查对应 adapter。
openclaw / teclaw 不注册(``has()`` 返回 False),其请求在 ``BaasBotService``
内走原始分支。

具体 adapter 实现的装配由 bootstrap 完成(core 不得 import plugins)。
"""

from __future__ import annotations

from collections.abc import Mapping

from secbaas.spi.bot.engine_adapter import BotEngineAdapter


class BotEngineAdapterRegistry:
    """按 engine_type 查 adapter 的注册表。"""

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
