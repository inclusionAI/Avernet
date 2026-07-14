"""
Embedding Providers

提供 Fake 和 Real 两种 Embedding Provider 实现。

使用方式：
    # Fake Provider（测试/开发）
    from src.infra.embedding.providers import FakeEmbeddingProvider
    provider = FakeEmbeddingProvider(dimension=4096)
    vector = provider.embed("some text")

    # Real Provider（生产环境）
    from src.infra.embedding.providers import RealEmbeddingProvider
    provider = RealEmbeddingProvider()  # 从环境变量加载配置
    vector = provider.embed("some text")

环境变量配置（Real Provider）：
    EMBEDDING_BASE_URL: Embedding API 基础 URL
    EMBEDDING_AUTH_TOKEN: 认证 token
    EMBEDDING_MODEL: 模型名称
    EMBEDDING_DIMENSION: 向量维度（默认 4096）
    EMBEDDING_TIMEOUT_MS: 请求超时（毫秒）
"""

from src.infra.embedding.providers.fake_provider import FakeEmbeddingProvider
from src.infra.embedding.providers.real_provider import (
    RealEmbeddingProvider,
    EmbeddingAPIError,
)


__all__ = [
    "FakeEmbeddingProvider",
    "RealEmbeddingProvider",
    "EmbeddingAPIError",
]