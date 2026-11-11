"""ContentStoreModule — the content store at the composition root (#3064).

One component, two consumers: the Manifest's fetch funnel files what it fetches
and reads the platform copy back for delivery and ``keep_last``; the Genome
Registry files what it produces and reads it back scoped by the rows it filed.
Both inject :class:`ContentStoreProtocol`, so the store binds once, here,
rather than inside either consumer's module.

Where the bytes live is the :class:`BlobStore` contract. This module binds the
default — the deployment's object storage, which every backend instance
shares. A profile whose object storage is a test double overrides the key in
its own column (``infrastructure/test/content_blob_store.py``); nothing here
branches on a profile or a setting.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.content_store import (
    BlobStore,
    ContentStoreProtocol,
    ContentStoreService,
    ObjectStorageBlobStore,
)
from agentclaw.community.core.repository.implementations.bot.content_store import (
    ContentStoreRepository,
)
from agentclaw.community.core.repository.protocols.bot import (
    ContentStoreRepositoryProtocol,
)
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


class ContentStoreModule(Module):
    """Bind the provenance-log repository, the default blob store, and the store over them."""

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
    def blob_store(self, storage: ObjectStoragePlugin) -> BlobStore:
        """The default medium: the deployment's object storage."""
        return ObjectStorageBlobStore(storage)

    @singleton
    @provider
    @inject
    def content_store(
        self,
        repository: ContentStoreRepositoryProtocol,
        blobs: BlobStore,
    ) -> ContentStoreProtocol:
        """The store over whichever blob store the profile wired."""
        return ContentStoreService(repository, blobs)
