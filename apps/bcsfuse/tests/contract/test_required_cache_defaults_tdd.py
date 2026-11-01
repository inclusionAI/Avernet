"""Deployment must compose query, response, and resilience caches by default."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import yaml


class StartupProvider:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class VersionedVectorStore:
    index_version = 9


def _context_with_config(config: Any):
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register("config", config)
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", VersionedVectorStore())
    return ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )


def test_deployment_yaml_enables_all_required_cache_layers_by_default() -> None:
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    root = Path(__file__).resolve().parents[2]
    config = YamlEnvConfigProvider(config_path=str(root / "configs/application.yaml"))

    assert config.get_bool("embedding.cache.enabled", False) is True
    assert config.get_int("embedding.cache.max_entries", 0) > 0
    assert config.get_float("embedding.cache.ttl_seconds", 0.0) > 0
    assert config.get_bool("search.result_cache.enabled", False) is True
    assert config.get_int("search.result_cache.max_entries", 0) > 0
    assert config.get_float("search.result_cache.ttl_seconds", 0.0) > 0
    assert config.get_bool("search.resilience.enabled", False) is True

    raw = yaml.safe_load((root / "configs/application.yaml").read_text()) or {}
    # Explicit defaults must be reviewable in the deployment contract, not only
    # hidden in Python fallback parameters.
    assert raw["embedding"]["cache"]["enabled"] is True
    assert raw["search"]["result_cache"]["enabled"] is True


def test_runtime_config_selects_query_batch_workers_for_capacity() -> None:
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    root = Path(__file__).resolve().parents[2]
    config = YamlEnvConfigProvider(config_path=str(root / "configs/application.yaml"))

    batch_size = config.get_int("embedding.cache.max_query_batch_size", 0)
    wait_seconds = config.get_float("embedding.cache.query_batch_wait_seconds", -1)
    workers = config.get_int("embedding.cache.max_query_batch_workers", 0)
    assert batch_size == 32
    assert wait_seconds > 0
    assert workers >= 2


def test_application_context_composes_a_live_result_cache() -> None:
    from app.application.agent_search_cache import AgentSearchResultCache
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    root = Path(__file__).resolve().parents[2]
    context = _context_with_config(
        YamlEnvConfigProvider(config_path=str(root / "configs/application.yaml"))
    )
    cache = AgentSearchResultCache.from_context(context)

    assert isinstance(cache, AgentSearchResultCache)
    assert cache.enabled is True
    assert cache.current_index_version() == 9
    assert cache.stats["max_entries"] > 0
    assert cache.stats["ttl_seconds"] > 0


class _DisabledResultCacheConfig:
    def get_bool(self, key: str, default: bool) -> bool:
        if key == "search.result_cache.enabled":
            return False
        return default

    def get_int(self, key: str, default: int) -> int:
        return default

    def get_float(self, key: str, default: float) -> float:
        return default


def test_result_cache_explicit_disable_fails_closed() -> None:
    """A disabled response cache is an invalid serving composition."""

    context = _context_with_config(_DisabledResultCacheConfig())

    from app.application.agent_search_cache import AgentSearchResultCache

    try:
        AgentSearchResultCache.from_context(context)
    except RuntimeError as error:
        assert "search.result_cache.enabled" in str(error)
    else:
        raise AssertionError("disabled result cache must fail closed")


def test_runtime_embedding_cache_disable_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.bootstrap.opensource import register_runtime_service_providers
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    for env_name in (
        "EMBEDDING_QUERY_CACHE_ENABLED",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_AUTH_TOKEN",
        "EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(env_name, raising=False)

    config_path = tmp_path / "application.yaml"
    config_path.write_text(
        "embedding:\n"
        "  cache:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    config = YamlEnvConfigProvider(config_path=str(config_path))

    try:
        register_runtime_service_providers(ProviderRegistry(), config)
    except RuntimeError as error:
        assert "embedding.cache.enabled" in str(error)
    else:
        raise AssertionError("disabled embedding cache must fail closed")
    assert not config_path.with_suffix(".lock").exists()


def test_runtime_share_embedding_call_budget_is_explicit() -> None:
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    root = Path(__file__).resolve().parents[2]
    config = YamlEnvConfigProvider(config_path=str(root / "configs/application.yaml"))

    assert config.get_int(
        "embedding.quota.max_calls_per_minute", 0
    ) > 0
    assert config.get_float("embedding.quota.period_seconds", 0.0) == 60.0
    assert config.get_float("embedding.quota.recovery_seconds", 0.0) > 0
