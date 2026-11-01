"""TDD contract for runtime Qdrant collection configuration."""

from __future__ import annotations

from pathlib import Path


def test_qdrant_collection_name_env_overrides_vector_collection(monkeypatch, tmp_path: Path) -> None:
    from app.infra.public.config.yaml_env_config_provider import YamlEnvConfigProvider

    monkeypatch.setenv("QDRANT_COLLECTION_NAME", "bcsfuse_live_validation_20261030")
    config = YamlEnvConfigProvider(config_path=str(tmp_path / "missing.yaml"))

    assert (
        config.get("vector.qdrant.collection", "bcsfuse_profiles")
        == "bcsfuse_live_validation_20261030"
    )
