"""
OpenAI Compatible LLM Provider - OSS Wrapper

Wraps existing OpenAI-compatible LLM provider for OSS compatibility.
"""
from src.infra.llm.providers.openai_compatible_provider import (
    OpenAICompatibleProvider as _OpenAICompatibleProvider,
)
from src.infra.llm.providers.openai_compatible_provider import (
    OpenAIProviderError,
    OpenAIAuthError,
    OpenAIProviderTimeout,
)


class OpenAICompatibleProvider(_OpenAICompatibleProvider):
    """
    OpenAI Compatible LLM Provider for OSS.

    This is a thin wrapper around the existing OpenAI-compatible provider
    to maintain consistent naming and future extensibility.

    Supports OpenAI Chat Completions API format.
    Requires LLM_BASE_URL and LLM_AUTH_TOKEN environment variables.
    """

    pass


__all__ = [
    "OpenAICompatibleProvider",
    "OpenAIProviderError",
    "OpenAIAuthError",
    "OpenAIProviderTimeout",
]
