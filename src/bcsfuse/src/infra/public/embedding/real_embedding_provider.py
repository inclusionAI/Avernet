"""
Real Embedding Provider - OSS Wrapper

Wraps existing real HTTP embedding provider for OSS compatibility.
"""
from src.infra.embedding.providers.real_provider import RealEmbeddingProvider as _RealEmbeddingProvider
from src.infra.embedding.providers.real_provider import EmbeddingAPIError


class RealEmbeddingProvider(_RealEmbeddingProvider):
    """
    Real Embedding Provider for OSS.

    This is a thin wrapper around the existing HTTP embedding provider
    to maintain consistent naming and future extensibility.

    Supports OpenAI-compatible API endpoints.
    Requires EMBEDDING_BASE_URL and EMBEDDING_AUTH_TOKEN environment variables.
    """

    pass