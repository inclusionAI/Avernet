"""The content store binds once, at the composition root, and both consumers get that one instance."""

from __future__ import annotations

from pathlib import Path

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
    assert store._blobs == (root if root.is_absolute() else Path.cwd() / root) / "blobs"


def test_the_store_is_a_singleton_the_genome_registry_shares() -> None:
    from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol

    injector = build_injector(profile=DeployProfile.TEST)
    store = injector.get(ContentStoreProtocol)
    assert injector.get(ContentStoreProtocol) is store
    assert injector.get(GenomeRegistryProtocol)._content is store
