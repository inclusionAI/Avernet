"""
Fake Embedding Provider - OSS Wrapper

Wraps existing fake embedding provider for OSS compatibility in tests.
"""
from src.infra.embedding.providers.fake_provider import FakeEmbeddingProvider as _FakeEmbeddingProvider


class FakeEmbeddingProvider(_FakeEmbeddingProvider):
    """
    Fake Embedding Provider for OSS testing.

    This is a thin wrapper around the existing fake provider
    to maintain consistent naming and future extensibility.

    Suitable for testing and development without external API dependencies.
    DO NOT use in production.
    """

    pass