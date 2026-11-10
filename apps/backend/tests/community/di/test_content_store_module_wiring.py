"""The content store binds once, at the composition root, and both consumers get that one instance."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentclaw.community.core.content_store import ContentStoreProtocol, ContentStoreService
from agentclaw.community.core.repository.implementations.bot.content_store import ContentStoreRepository
from agentclaw.community.core.repository.protocols.bot import ContentStoreRepositoryProtocol
from agentclaw.community.di import config as cfg
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile


def test_the_store_resolves_over_the_repository_and_the_configured_root() -> None:
    injector = build_injector(profile=DeployProfile.TEST)
    store = injector.get(ContentStoreProtocol)
    assert isinstance(store, ContentStoreService)
    assert isinstance(injector.get(ContentStoreRepositoryProtocol), ContentStoreRepository)
    root = Path(injector.get(cfg.BotConfigManifestConfig).content_store_dir).expanduser()
    assert store._blobs.root == (root if root.is_absolute() else Path.cwd() / root) / "blobs"


def test_the_store_is_a_singleton_the_genome_registry_shares() -> None:
    from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol

    injector = build_injector(profile=DeployProfile.TEST)
    store = injector.get(ContentStoreProtocol)
    assert injector.get(ContentStoreProtocol) is store
    assert injector.get(GenomeRegistryProtocol)._content is store


class _Resolver:
    """Stands in for the injector: hands out one object storage, or refuses to be asked."""

    def __init__(self, storage=None) -> None:
        self._storage = storage

    def get(self, interface):
        if self._storage is None:
            raise AssertionError("the fs backend must not resolve the object storage")
        return self._storage


def _provide(config: cfg.BotConfigManifestConfig, storage=None) -> ContentStoreService:
    """The module's provider, called directly over a given config and (optionally) object storage."""
    from agentclaw.community.di.modules.content_store_module import ContentStoreModule

    return ContentStoreModule().content_store(object(), config, _Resolver(storage))


def test_the_test_profile_keeps_the_local_tree() -> None:
    from agentclaw.community.core.content_store.blobs import LocalBlobStore

    injector = build_injector(profile=DeployProfile.TEST)
    assert injector.get(cfg.BotConfigManifestConfig).content_store_backend == "fs"
    assert isinstance(injector.get(ContentStoreProtocol)._blobs, LocalBlobStore)


def test_backend_fs_wires_local_store(tmp_path) -> None:
    from agentclaw.community.core.content_store.blobs import LocalBlobStore

    config = cfg.BotConfigManifestConfig(content_store_backend="fs", content_store_dir=str(tmp_path))
    store = _provide(config)
    assert isinstance(store._blobs, LocalBlobStore)
    assert store._blobs.root == tmp_path / "blobs"


def test_backend_object_storage_wires_object_store_with_legacy_tree(tmp_path) -> None:
    from agentclaw.community.core.content_store.blobs import LocalBlobStore, ObjectStorageBlobStore
    from agentclaw.community.plugins.community.object_storage import CommunityFsObjectStorage

    storage = CommunityFsObjectStorage(str(tmp_path / "objstore"))
    config = cfg.BotConfigManifestConfig(content_store_dir=str(tmp_path / "old"))
    assert config.content_store_backend == "object_storage"
    store = _provide(config, storage)

    assert isinstance(store._blobs, ObjectStorageBlobStore)
    assert store._blobs._storage is storage
    assert isinstance(store._blobs._legacy, LocalBlobStore)
    assert store._blobs._legacy.root == tmp_path / "old" / "blobs"
    # Round trip through the filesystem object storage a community install binds by default.
    digest = "sha256:" + __import__("hashlib").sha256(b"bytes").hexdigest()
    store._blobs.write(digest, b"bytes")
    assert store.read(digest) == b"bytes"


def test_unknown_backend_fails_at_startup() -> None:
    from agentclaw.community.core.content_store.settings import (
        DEFAULT_CONTENT_STORE_BACKEND,
        content_store_backend_from_config,
    )

    assert content_store_backend_from_config({}) == DEFAULT_CONTENT_STORE_BACKEND == "object_storage"
    assert content_store_backend_from_config({"bot_config_manifest": None}) == "object_storage"
    assert content_store_backend_from_config({"bot_config_manifest": {}}) == "object_storage"
    assert content_store_backend_from_config({"bot_config_manifest": {"content_store_backend": "fs"}}) == "fs"
    for bad in ("nas", "", None, 3):
        with pytest.raises(ValueError, match="content_store_backend"):
            content_store_backend_from_config({"bot_config_manifest": {"content_store_backend": bad}})
    with pytest.raises(ValueError, match="mapping"):
        content_store_backend_from_config({"bot_config_manifest": "fs"})
