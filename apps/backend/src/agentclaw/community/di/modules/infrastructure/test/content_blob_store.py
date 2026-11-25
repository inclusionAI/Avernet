"""Content-store blob concern — test / singlebox binding (a local directory tree).

These profiles bind a test double for object storage that keeps nothing, so
the content store's bytes live on local disk instead: one backend instance,
one directory. Overrides the default ``BlobStore`` that ``ContentStoreModule``
binds over the deployment's object storage.
"""
from __future__ import annotations

from pathlib import Path

from injector import Module, inject, provider, singleton

from agentclaw.community.core.content_store import BlobStore
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger


logger = get_logger()


class TestContentBlobStoreModule(Module):
    """test / singlebox: the blobs on the local tree under ``content_store_dir``."""

    @singleton
    @provider
    @inject
    def blob_store(self, manifest_config: cfg.BotConfigManifestConfig) -> BlobStore:
        from agentclaw.community.testing.content_blob_store import LocalBlobStore

        logger.info("BlobStore: LocalBlobStore (test)")
        return LocalBlobStore(Path(manifest_config.content_store_dir))
