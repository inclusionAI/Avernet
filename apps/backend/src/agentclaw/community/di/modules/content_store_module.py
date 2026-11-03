"""ContentStoreModule — the content store at the composition root (#3064).

One component, two consumers: the Manifest's fetch funnel files what it fetches
and reads the platform copy back for delivery and ``keep_last``; the Genome
Registry files what it produces and reads it back scoped by the rows it filed.
Both inject :class:`ContentStoreProtocol`, so the store binds once, here,
rather than inside either consumer's module.

The blob root is a ``user_config.bot_config_manifest.content_store_dir``
decision. The one yaml read stays in ``ManifestFetchModule.bot_config_manifest``
(the typed ``cfg.BotConfigManifestConfig`` cluster, parsed by the store's own
``content_store_root_from_config``); this module only consumes the parsed
value, so the key keeps its name and its deployment overlays keep working.
"""
from __future__ import annotations

from pathlib import Path

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.content_store import (
    ContentStoreProtocol,
    ContentStoreService,
)
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
    ) -> ContentStoreProtocol:
        """The store: blob root from the typed config cluster, never an environment read."""
        return ContentStoreService(
            repository,
            Path(manifest_config.content_store_dir),
        )
