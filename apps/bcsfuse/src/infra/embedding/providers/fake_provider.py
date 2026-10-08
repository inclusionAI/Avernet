"""
FakeEmbeddingProvider

用于测试和开发环境的伪 Embedding Provider。

生成确定性伪向量，不依赖外部 API。
"""

from __future__ import annotations

import hashlib
from typing import Optional

from src.domain.services.embedding_provider import EmbeddingProvider


class FakeEmbeddingProvider(EmbeddingProvider):
    """
    Fake Embedding Provider

    生成确定性伪向量，用于测试和开发环境。

    特点：
    - 不依赖外部 API
    - 相同输入产生相同输出（确定性）
    - 向量值基于文本 hash 生成
    - 适合单元测试和 CI/CD

    Attributes:
        _dimension: 向量维度
        _seed: 随机种子（用于额外随机性）
    """

    def __init__(
        self,
        dimension: int = 4096,
        seed: Optional[int] = None,
    ):
        """
        初始化 Fake Provider

        Args:
            dimension: 向量维度（默认 4096，与 Qwen3-Embedding-8B 一致）
            seed: 可选的随机种子，用于额外随机性
        """
        self._dimension = dimension
        self._seed = seed or 42

    def embed(self, text: str) -> list[float]:
        """
        生成文本的伪 embedding 向量

        使用 SHA256 hash 将文本转换为确定性浮点向量。

        Args:
            text: 输入文本

        Returns:
            list[float]: 伪 embedding 向量
        """
        # 使用文本内容生成确定性向量
        vector = self._text_to_vector(text)
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        批量生成伪 embedding 向量

        Args:
            texts: 输入文本列表

        Returns:
            list[list[float]]: 伪 embedding 向量列表
        """
        return [self.embed(text) for text in texts]

    @property
    def dimension(self) -> int:
        """
        返回向量维度

        Returns:
            int: 向量维度
        """
        return self._dimension

    def _text_to_vector(self, text: str) -> list[float]:
        """
        将文本转换为确定性向量

        使用 hash 函数生成伪向量，确保：
        1. 相同输入产生相同输出
        2. 不同输入产生不同输出
        3. 向量值在 [-1, 1] 范围内

        Args:
            text: 输入文本

        Returns:
            list[float]: 确定性伪向量
        """
        vector = []

        for i in range(self._dimension):
            # 为每个维度生成不同的 hash
            hash_input = f"{self._seed}:{text}:{i}"
            hash_value = hashlib.sha256(hash_input.encode()).hexdigest()

            # 将 hash 的前 8 个字符转换为浮点数
            int_value = int(hash_value[:8], 16)
            # 映射到 [-1, 1] 范围
            float_value = (int_value / 0xFFFFFFFF) * 2 - 1

            vector.append(float_value)

        # 归一化向量（可选，使向量更像真实 embedding）
        norm = sum(x * x for x in vector) ** 0.5
        if norm > 0:
            vector = [x / norm for x in vector]

        return vector


__all__ = [
    "FakeEmbeddingProvider",
]