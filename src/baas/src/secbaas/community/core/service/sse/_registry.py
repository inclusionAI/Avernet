"""SSE converter 工厂

提供 SseConverterFactory，通过 DI 注入的 converter 工厂映射，
按名称创建 StreamConverter 实例。
"""

from __future__ import annotations

from collections.abc import Callable

from secbaas.community.api.sse import StreamConverter


class SseConverterFactory:
    """StreamConverter 工厂

    通过构造函数接收 converter 工厂函数映射，按名称创建新实例。
    默认使用 "default" converter。
    """

    def __init__(
        self,
        converter_factories: dict[str, Callable[[], StreamConverter]],
        default_name: str = "default",
    ) -> None:
        self._factories = converter_factories
        self._default_name = default_name

    def create(self, name: str | None = None) -> StreamConverter:
        """创建 converter 实例

        Args:
            name: converter 名称，None 时使用 default_name

        Returns:
            StreamConverter 实例

        Raises:
            KeyError: 名称未注册
        """
        key = name or self._default_name
        factory = self._factories.get(key)
        if factory is None:
            raise KeyError(f"SSE converter not registered: {key}")
        return factory()
