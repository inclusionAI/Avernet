"""The content store binds once, at the composition root, and both consumers get that one instance."""

from __future__ import annotations

from pathlib import Path

import pytest
from injector import Injector, Module, provider, singleton

from agentclaw.community.core.content_store import ContentStoreProtocol, ContentStoreService
from agentclaw.community.core.repository.implementations.bot.content_store import ContentStoreRepository
from agentclaw.community.core.repository.protocols.bot import ContentStoreRepositoryProtocol
from agentclaw.community.di import config as cfg
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


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


def test_the_test_profile_wires_the_local_blob_store() -> None:
    from agentclaw.community.testing.content_blob_store import LocalBlobStore

    injector = build_injector(profile=DeployProfile.TEST)
    assert isinstance(injector.get(ContentStoreProtocol)._blobs, LocalBlobStore)


def test_the_default_binding_is_the_object_storage_blob_store(tmp_path) -> None:
    """A deployment's column leaves ``BlobStore`` alone, so it gets the object storage every instance shares."""
    from agentclaw.community.core.content_store import BlobStore, ObjectStorageBlobStore
    from agentclaw.community.di.modules.content_store_module import ContentStoreModule
    from agentclaw.community.plugins.community.object_storage import CommunityFsObjectStorage

    storage = CommunityFsObjectStorage(str(tmp_path / "objstore"))

    class _Infra(Module):
        @singleton
        @provider
        def object_storage(self) -> ObjectStoragePlugin:
            return storage

    blobs = Injector([_Infra(), ContentStoreModule()]).get(BlobStore)
    assert isinstance(blobs, ObjectStorageBlobStore)
    assert blobs._storage is storage


@pytest.mark.parametrize("profile", [DeployProfile.TEST, DeployProfile.SINGLEBOX])
def test_profiles_whose_object_storage_is_a_mock_override_the_blob_store(profile) -> None:
    """test and singlebox bind MockObjectStoragePlugin, which keeps nothing: their column wires the local tree."""
    from agentclaw.community.di.modules.infrastructure.test.content_blob_store import TestContentBlobStoreModule
    from agentclaw.community.di.profile_modules import modules_for

    assert any(isinstance(module, TestContentBlobStoreModule) for module in modules_for(profile))
