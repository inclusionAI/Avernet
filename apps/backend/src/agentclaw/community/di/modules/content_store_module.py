"""ContentStoreModule — the content store at the composition root (#3064).

One component, two consumers: the Manifest's fetch funnel files what it fetches
and reads the platform copy back for delivery and ``keep_last``; the Genome
Registry files what it produces and reads it back scoped by the rows it filed.
Both inject :class:`ContentStoreProtocol`, so the store binds once, here,
rather than inside either consumer's module.

Where the blobs live is a ``user_config.bot_config_manifest`` decision:
``content_store_backend`` picks the medium and ``content_store_dir`` is the
local tree. The one yaml read stays in ``ManifestFetchModule.bot_config_manifest``
(the typed ``cfg.BotConfigManifestConfig`` cluster, parsed by the store's own
``settings`` readers); this module only consumes the parsed values, so the
keys keep their names and their deployment overlays keep working.
"""
from __future__ import annotations

from pathlib import Path

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.core.content_store import (
    ContentStoreProtocol,
    ContentStoreService,
    LocalBlobStore,
)
from agentclaw.community.core.content_store.blobs import ObjectStorageBlobStore
from agentclaw.community.core.content_store.settings import BACKEND_FS
from agentclaw.community.core.repository.implementations.bot.content_store import (
    ContentStoreRepository,
)
from agentclaw.community.core.repository.protocols.bot import (
    ContentStoreRepositoryProtocol,
)
from agentclaw.community.di import config as cfg


class ContentStoreModule(Module):
    """Bind the provenance-log repository and provide the store over it."""

    def configure(self, binder: Binder) -> None:
        # The append-only provenance log behind the store: one ORM body, the
        # injected DatabasePlugin is the only per-profile difference.
        binder.bind(
            ContentStoreRepositoryProtocol,
            to=ContentStoreRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def content_store(
        self,
        repository: ContentStoreRepositoryProtocol,
        manifest_config: cfg.BotConfigManifestConfig,
        injector: Injector,
    ) -> ContentStoreProtocol:
        """The store over the configured medium, from the typed config cluster, never an environment read.

        The local tree is the store under ``fs``. Under ``object_storage``
        it is what the object store reads through to, for blobs an earlier
        version wrote to this instance's disk. The object storage plugin is
        resolved only when it is the chosen medium.
        """
        local = LocalBlobStore(Path(manifest_config.content_store_dir))
        if manifest_config.content_store_backend == BACKEND_FS:
            return ContentStoreService(repository, blobs=local)
        from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin

        return ContentStoreService(
            repository,
            blobs=ObjectStorageBlobStore(injector.get(ObjectStoragePlugin), legacy=local),
        )
