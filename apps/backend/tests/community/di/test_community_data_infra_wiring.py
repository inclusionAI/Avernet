"""Community data-plane column wiring (B3).

Pins that the community profile binds the data-plane Protocols (secret /
database / cache / object storage) to their real community implementations,
and that none is a ``MockSeam`` test double (the community column ships real,
deployable impls). Grown one concern per task across B3.
"""
from __future__ import annotations

import pytest

from agentclaw.community.di import config_community as cfg
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.modules.infrastructure.community.database import (
    CommunityDatabaseModule,
)
from agentclaw.community.di.modules.infrastructure.community.object_storage import (
    CommunityObjectStorageModule,
)
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugins.community.cache import CommunityCache
from agentclaw.community.plugins.community.database import CommunityDatabase
from agentclaw.community.plugins.community.object_storage import (
    CommunityFsObjectStorage,
    CommunityS3ObjectStorage,
)
from agentclaw.community.plugins.community.secret_resolver import CommunitySecretResolver
from agentclaw.community.plugins.local._mock_seam import MockSeam


@pytest.fixture(scope="module")
def community_injector():
    return build_injector(profile=DeployProfile.COMMUNITY)


def test_community_binds_secret_resolver(community_injector):
    resolved = community_injector.get(SecretResolver)
    assert isinstance(resolved, CommunitySecretResolver)
    assert not isinstance(resolved, MockSeam)


def test_community_binds_database(community_injector):
    # Construction is lazy (create_engine opens no connection), so resolving the
    # binding does not touch the filesystem.
    resolved = community_injector.get(DatabasePlugin)
    assert isinstance(resolved, CommunityDatabase)
    assert not isinstance(resolved, MockSeam)


def test_database_config_reads_the_yaml_block_verbatim(monkeypatch):
    # DATABASE_URL no longer reaches the provider directly: the shipped overlay
    # spells the url as ``${DATABASE_URL:-...}`` and the YAML loader expands it
    # during config loading (AGENTS.md puts raw env access there). The provider
    # is therefore a pure reader of the resolved block.
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/ignored-by-the-provider.db")
    monkeypatch.setattr(
        "agentclaw.community.di.modules.config_module._block",
        lambda name: {"backend": "sqlite", "url": "sqlite:///tmp/from-yaml.db"},
    )
    config = CommunityDatabaseModule().database_config()
    assert config.url == "sqlite:///tmp/from-yaml.db"


def test_database_config_defaults_are_sqlite_with_schema_creation():
    config = CommunityDatabaseModule().database_config()
    assert config.backend == "sqlite"
    assert config.url.startswith("sqlite")
    # A container deployment has nobody to run DDL before the first request.
    assert config.create_schema is True


def test_database_config_rejects_backend_url_mismatch(monkeypatch):
    # Flipping backend to mysql but leaving the sqlite url behind must fail loudly
    # rather than quietly writing production traffic to a file in the container.
    monkeypatch.setattr(
        "agentclaw.community.di.modules.config_module._block",
        lambda name: {"backend": "mysql", "url": "sqlite:///./data/agentclaw.db"},
    )
    with pytest.raises(ValueError, match="database.backend is 'mysql'"):
        CommunityDatabaseModule().database_config()


def test_database_config_rejects_unknown_backend(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.di.modules.config_module._block",
        lambda name: {"backend": "oracle", "url": "oracle://host/db"},
    )
    with pytest.raises(ValueError, match="Unknown database.backend"):
        CommunityDatabaseModule().database_config()


def test_mysql_backend_builds_a_pooled_engine(monkeypatch):
    # mysql+pymysql is lazy — create_engine opens no socket — so this pins the
    # engine setup (pre-ping / recycle) without needing a server.
    monkeypatch.setattr(
        "agentclaw.community.di.modules.config_module._block",
        lambda name: {
            "backend": "mysql",
            "url": "mysql+pymysql://u:p@db.example:3306/agentclaw?charset=utf8mb4",
            "create_schema": False,
        },
    )
    config = CommunityDatabaseModule().database_config()
    plugin = CommunityDatabaseModule().database(config)
    assert isinstance(plugin, CommunityDatabase)
    assert plugin._engine.pool._pre_ping is True
    assert plugin._engine.pool._recycle == 3600


def test_community_binds_cache(community_injector):
    resolved = community_injector.get(CachePlugin)
    assert isinstance(resolved, CommunityCache)
    assert not isinstance(resolved, MockSeam)
    # Default config (no REDIS_URL) → in-process backend; KV round-trips.
    assert resolved.set("b3-wire", "ok") is True
    assert resolved.get("b3-wire") == "ok"


def test_community_binds_object_storage_fs_default(community_injector):
    # Default config selects the filesystem impl; construction is side-effect-
    # free (no directory created until a write).
    resolved = community_injector.get(ObjectStoragePlugin)
    assert isinstance(resolved, CommunityFsObjectStorage)
    assert not isinstance(resolved, MockSeam)


def test_object_storage_provider_selects_fs_or_s3():
    module = CommunityObjectStorageModule()
    fs = module.object_storage(
        cfg.CommunityObjectStorageConfig(backend="fs", fs_root="./x")
    )
    assert isinstance(fs, CommunityFsObjectStorage)
    s3 = module.object_storage(
        cfg.CommunityObjectStorageConfig(
            backend="s3", s3=cfg.CommunityS3Config(bucket="b")
        )
    )
    assert isinstance(s3, CommunityS3ObjectStorage)


def test_object_storage_unknown_backend_fails_fast():
    module = CommunityObjectStorageModule()
    with pytest.raises(ValueError, match="Unknown object_storage.backend"):
        module.object_storage(
            cfg.CommunityObjectStorageConfig(backend="minio")
        )


def test_object_storage_config_builds_nested_s3():
    config = CommunityObjectStorageModule().object_storage_config()
    assert config.backend == "fs"  # default when no yaml block under test profile
    assert isinstance(config.s3, cfg.CommunityS3Config)
