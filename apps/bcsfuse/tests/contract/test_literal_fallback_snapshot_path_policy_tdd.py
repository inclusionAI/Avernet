"""TDD: runtime fallback snapshots must live on a deployment-persistent path."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest


@pytest.fixture
def runtime_config():
    from app.bootstrap import opensource
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    config_path = (
        Path(opensource.__file__).parents[3]
        / "configs"
        / "application.yaml"
    )
    return YamlEnvConfigProvider(config_path=str(config_path))


def test_runtime_snapshot_rejects_ephemeral_path(monkeypatch, runtime_config):
    from app.bootstrap import opensource
    from app.infra.public.stores.literal_fallback_path_policy import (
        EphemeralSnapshotPathError,
    )

    monkeypatch.setenv(
        "BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH",
        "/tmp/bcsfuse_literal_fallback_snapshot.json",
    )

    with pytest.raises(EphemeralSnapshotPathError):
        opensource._literal_fallback_snapshot_store(runtime_config, enabled=True)


def test_runtime_snapshot_requires_explicit_persistent_path(monkeypatch, runtime_config):
    from app.bootstrap import opensource
    from app.infra.public.stores.literal_fallback_path_policy import (
        EphemeralSnapshotPathError,
    )

    monkeypatch.delenv("BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH", raising=False)

    with pytest.raises(EphemeralSnapshotPathError):
        opensource._literal_fallback_snapshot_store(runtime_config, enabled=True)


def test_runtime_snapshot_allows_explicit_persistent_path(monkeypatch, runtime_config):
    from app.bootstrap import opensource

    monkeypatch.setenv(
        "BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH",
        "/var/lib/bcsfuse/literal_fallback_snapshot.json",
    )

    store = opensource._literal_fallback_snapshot_store(runtime_config, enabled=True)

    expected_path = Path(
        "/var/lib/bcsfuse/literal_fallback_snapshot.json"
    ).resolve(strict=False)
    assert store is not None
    assert store.path == expected_path


def test_snapshot_path_resolves_ephemeral_symlinks(monkeypatch, runtime_config):
    from pathlib import Path

    from app.bootstrap import opensource
    from app.infra.public.stores.literal_fallback_path_policy import (
        EphemeralSnapshotPathError,
    )

    real_target = Path("/tmp/bcsfuse_real_snapshot.json")
    symlink_path = Path("/var/lib/bcsfuse/bcsfuse_link_snapshot.json")
    monkeypatch.setenv("BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH", str(symlink_path))
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda self, strict=False: real_target,
    )

    with pytest.raises(EphemeralSnapshotPathError):
        opensource._literal_fallback_snapshot_store(runtime_config, enabled=True)


def test_snapshot_path_policy_is_wired_into_runtime_composition(runtime_config):
    from app.bootstrap import opensource

    composition_source = inspect.getsource(opensource._build_runtime_providers)
    helper_source = inspect.getsource(opensource._literal_fallback_snapshot_store)

    assert "_literal_fallback_snapshot_store(config, enabled=True)" in composition_source
    assert "LiteralFallbackSnapshotPathPolicy().validate_runtime_path" in helper_source
    assert "EphemeralSnapshotPathError" in helper_source
