"""
EmbeddingProvider

Embedding Provider 协议接口。

定义所有 embedding provider 必须实现的接口。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """
    Embedding Provider 协议

    定义所有 embedding provider 必须实现的接口。

    Methods:
        embed: 生成文本的 embedding 向量
        dimension: 返回 embedding 向量的维度
    """

    def embed(self, text: str) -> list[float]:
        """
        生成文本的 embedding 向量

        Args:
            text: 输入文本

        Returns:
            list[float]: Embedding 向量
        """
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        批量生成文本的 embedding 向量

        默认实现：逐个调用 embed 方法。
        子类可重写以实现真正的批量处理。

        Args:
            texts: 输入文本列表

        Returns:
            list[list[float]]: Embedding 向量列表
        """
        return [self.embed(text) for text in texts]

    @property
    def dimension(self) -> int:
        """
        返回 embedding 向量的维度

        Returns:
            int: 向量维度
        """
        ...


__all__ = [
    "EmbeddingProvider",
]