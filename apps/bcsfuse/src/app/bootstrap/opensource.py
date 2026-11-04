"""
OpenSource Provider Builder

Builds provider registry for OSS deployments in different modes.
"""

import logging
import os
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

logger = logging.getLogger(__name__)


def _safe_host(url: str | None) -> str:
    """Extract safe host from URL for logging (no secrets)."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return parsed.netloc or parsed.path.split("/")[0]
    except (ValueError, TypeError, AttributeError):
        return "<invalid-url>"


def register_runtime_service_providers(
    registry: "ProviderRegistry", config: "YamlEnvConfigProvider"
) -> None:
    """Register real public HTTP providers without selecting storage.

    Internal compositions use this seam to keep Avernet-owned embedding, LLM,
    and reranker behavior while injecting their own durable stores first.
    """
    from app.infra.embedding.config.embedding_settings import EmbeddingSettings
    from app.infra.llm.config.llm_settings import LLMSettings
    from app.infra.public.embedding.real_embedding_provider import (
        RealEmbeddingProvider,
    )
    from app.infra.public.llm.anthropic_compatible_provider import (
        AnthropicCompatibleProvider,
    )
    from app.infra.public.reranker.http_reranker import HttpReranker

    embedding_base_url = (
        os.getenv("EMBEDDING_BASE_URL") or config.get("embedding.base_url") or ""
    )
    embedding_model = (
        os.getenv("EMBEDDING_MODEL")
        or config.get("embedding.model")
        or "Qwen3-Embedding-8B"
    )
    embedding_dimension = int(
        os.getenv("EMBEDDING_DIMENSION") or config.get("embedding.dimension") or 4096
    )

    logger.info(
        "[EMBEDDING_CONFIG_RESOLUTION] provider_mode=%s enabled=%s provider_class=%s "
        "base_url_host=%s model=%s dimension=%s env_base_url_present=%s env_model_present=%s "
        "env_token_present=%s env_dimension_present=%s fallback_default_used=%s",
        "runtime",
        True,
        "RealEmbeddingProvider",
        _safe_host(embedding_base_url),
        embedding_model,
        embedding_dimension,
        "YES" if os.getenv("EMBEDDING_BASE_URL") else "NO",
        "YES" if os.getenv("EMBEDDING_MODEL") else "NO",
        "YES" if os.getenv("EMBEDDING_AUTH_TOKEN") else "NO",
        "YES" if os.getenv("EMBEDDING_DIMENSION") else "NO",
        "YES"
        if not os.getenv("EMBEDDING_BASE_URL") and not config.get("embedding.base_url")
        else "NO",
    )

    registry.register(
        "embedding_provider",
        RealEmbeddingProvider(
            settings=EmbeddingSettings(
                base_url=embedding_base_url,
                auth_token=os.getenv("EMBEDDING_AUTH_TOKEN")
                or config.get("embedding.auth_token"),
                model=embedding_model,
                dimension=embedding_dimension,
            )
        ),
    )
    registry.register("reranker_provider", HttpReranker())
    registry.register(
        "llm_provider",
        AnthropicCompatibleProvider(
            settings=LLMSettings(
                base_url=os.getenv("LLM_BASE_URL") or config.get("llm.base_url"),
                auth_token=os.getenv("LLM_AUTH_TOKEN") or config.get("llm.auth_token"),
                fast_model=(
                    os.getenv("LLM_FAST_MODEL")
                    or config.get("llm.fast_model")
                    or "claude-3-sonnet"
                ),
                reasoning_model=(
                    os.getenv("LLM_REASONING_MODEL")
                    or config.get("llm.reasoning_model")
                    or "claude-3-opus"
                ),
            )
        ),
    )


def build_opensource_provider_registry(mode: str = "runtime") -> "ProviderRegistry":
    """TC public dependencies; storage is composed lazily after host startup."""
    if mode not in {"runtime", "dev", "dev_smoke", "test"}:
        raise ValueError("invalid TC provider mode")
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.auth.simple_token_auth_provider import SimpleTokenAuthProvider
    from app.infra.public.cache.in_memory_cache_provider import InMemoryCacheProvider
    from app.infra.public.config.yaml_env_config_provider import YamlEnvConfigProvider
    from app.providers.public.env_secret_provider import EnvSecretProvider
    from app.providers.public.noop_context_provider import NoopContextProvider
    from app.providers.public.noop_startup_provider import NoopStartupProvider
    from app.tc.providers import configure_models

    registry = ProviderRegistry()
    config = YamlEnvConfigProvider()
    registry.register("config", config)
    registry.register("secret_provider", EnvSecretProvider())
    registry.register("startup_provider", NoopStartupProvider())
    registry.register("context_provider", NoopContextProvider())
    registry.register("auth", SimpleTokenAuthProvider())
    registry.register("cache_provider", InMemoryCacheProvider())
    if mode in {"test", "dev_smoke"}:
        configure_models(registry, config, mode)
    else:
        registry.register(
            "tc_model_factory", lambda: configure_models(registry, config, mode)
        )
    return registry
