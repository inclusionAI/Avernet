"""
Fake LLM Provider - OSS Wrapper

Wraps existing fake LLM provider for OSS compatibility in tests.
"""
from src.infra.llm.providers.fake_provider import FakeLLMProvider as _FakeLLMProvider


class FakeLLMProvider(_FakeLLMProvider):
    """
    Fake LLM Provider for OSS testing.

    This is a thin wrapper around the existing fake provider
    to maintain consistent naming and future extensibility.

    Suitable for testing and development without external API dependencies.
    DO NOT use in production.
    """

    pass