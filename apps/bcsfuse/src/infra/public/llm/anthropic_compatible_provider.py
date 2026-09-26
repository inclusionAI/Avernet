"""
Anthropic Compatible LLM Provider - OSS Wrapper

Wraps existing Anthropic-compatible LLM provider for OSS compatibility.
"""
from src.infra.llm.providers.anthropic_compatible_provider import (
    AnthropicCompatibleProvider as _AnthropicCompatibleProvider,
)
from src.infra.llm.providers.anthropic_compatible_provider import (
    AnthropicProviderError,
    AnthropicAuthError,
    AnthropicProviderTimeout,
)


class AnthropicCompatibleProvider(_AnthropicCompatibleProvider):
    """
    Anthropic Compatible LLM Provider for OSS.

    This is a thin wrapper around the existing Anthropic-compatible provider
    to maintain consistent naming and future extensibility.

    Supports Anthropic Messages API format.
    Requires LLM_BASE_URL and LLM_AUTH_TOKEN environment variables.
    """

    pass