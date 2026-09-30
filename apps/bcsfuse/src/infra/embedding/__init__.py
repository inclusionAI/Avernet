"""
Embedding Infrastructure Module

提供 Embedding 相关的基础设施组件。
"""

from src.infra.embedding.config.embedding_settings import EmbeddingSettings
from src.infra.embedding.providers import (
    FakeEmbeddingProvider,
    RealEmbeddingProvider,
    EmbeddingAPIError,
)


def create_embedding_provider(
    mode: str = "fake",
    dimension: int = 4096,
    seed: int = 42,
    settings: EmbeddingSettings | None = None,
):
    """
    创建 Embedding Provider 的工厂函数

    Args:
        mode: Provider 模式，可选 "fake" 或 "real"
        dimension: 向量维度（仅 fake 模式使用）
        seed: 随机种子（仅 fake 模式使用）
        settings: Embedding 配置（仅 real 模式使用，为 None 时从环境变量加载）

    Returns:
        EmbeddingProvider 实例

    Raises:
        ValueError: mode 参数无效时抛出

    Example:
        # 创建 fake provider（测试）
        provider = create_embedding_provider(mode="fake", dimension=384)

        # 创建 real provider（生产）
        provider = create_embedding_provider(mode="real")
    """
    if mode == "fake":
        return FakeEmbeddingProvider(dimension=dimension, seed=seed)
    elif mode == "real":
        if settings is None:
            settings = EmbeddingSettings()
        return RealEmbeddingProvider(settings=settings)
    else:
        raise ValueError(f"无效的 embedding mode: {mode}。可选: 'fake', 'real'")


__all__ = [
    "EmbeddingSettings",
    "FakeEmbeddingProvider",
    "RealEmbeddingProvider",
    "EmbeddingAPIError",
    "create_embedding_provider",
]