"""TDD contract for composed YAML configuration resolution."""

from __future__ import annotations

from pathlib import Path


def test_default_uses_the_profile_agnostic_repository_yaml(monkeypatch) -> None:
    """The opensource profile file is not currently split by profile."""

    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    monkeypatch.delenv("BCSFUSE_CONFIG_PATH", raising=False)
    monkeypatch.delenv("EMBEDDING_QUERY_BATCH_MAX_SIZE", raising=False)
    monkeypatch.chdir(
        Path(__file__).resolve().parents[2]
    )

    config = YamlEnvConfigProvider()

    assert config.get_int("embedding.cache.max_query_batch_size", 16) == 32
    assert config.get("embedding.request_dimensions") is True


def test_bcsfuse_config_path_is_honored(monkeypatch, tmp_path) -> None:
    """Operators must be able to supply an isolated profile file."""

    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    config_path = tmp_path / "application-isolated.yaml"
    config_path.write_text(
        "search:\n  capacity:\n    max_inflight: 37\n", encoding="utf-8"
    )
    monkeypatch.setenv("BCSFUSE_CONFIG_PATH", str(config_path))
    monkeypatch.chdir(tmp_path)

    config = YamlEnvConfigProvider()

    assert config.get_int("search.capacity.max_inflight", 128) == 37


def test_repository_yaml_admits_two_hundred_concurrent_searches(
    monkeypatch,
) -> None:
    """The serving profile must not queue away a 200-request burst."""

    from app.bootstrap.search_capacity import SearchCapacitySettings
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    monkeypatch.delenv("BCSFUSE_CONFIG_PATH", raising=False)
    monkeypatch.delenv("BCSFUSE_SEARCH_CAPACITY_MAX_INFLIGHT", raising=False)
    monkeypatch.delenv("BCSFUSE_SEARCH_CAPACITY_MAX_QUEUE", raising=False)
    monkeypatch.chdir(Path(__file__).resolve().parents[2])

    class Context:
        config = YamlEnvConfigProvider()

    settings = SearchCapacitySettings.from_context(Context())

    assert settings.max_inflight == 256
    assert settings.max_queue == 256
