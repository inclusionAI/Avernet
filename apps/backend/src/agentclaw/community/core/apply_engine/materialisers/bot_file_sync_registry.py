"""Per-engine ``BotFileSyncPort`` resolution — one registry, one delegating port.

A sync's shape differs per engine family: an openclaw bot's workspace lives on
the NAS merge area and converges by ``sudo rsync``; an aicoding bot's workspace
lives on fastdisk and syncs through the container's ``service_publish`` chain;
a teclaw bot is composed by its artifact, so there is no sync at all. The
materialiser must not pick — it calls one port, and the port asks the registry
which strategy the **destination** engine runs.

This mirrors :class:`ArtifactTransferRegistry`: a ``Mapping[engine_type,
strategy]`` resolved per call, with a default that refuses rather than silently
succeeding. The default is the existing :class:`NoopBotFileSyncPort`, so an
engine the composition root did not register an implementation for lands on the
"not yet implemented" refusal the materialiser already translates to a FAILED
entry — legible in the apply report, never a silent success.

The composition root registers one strategy per engine that has a real
implementation; '' (a bot whose engine_type is empty) is likewise unrouted and
refuses. Resolving uses the **destination's** engine, because the destination
is the one whose storage model the sync writes into, and copy preserves the
source's engine onto a fresh bot of the same engine (a ``BotCreateSpec`` whose
``engine_type`` came from the source).
"""
from __future__ import annotations

from typing import Mapping

from agentclaw.community.core.apply_engine.materialisers.bot_file_sync_noop import (
    NoopBotFileSyncPort,
)
from agentclaw.community.core.ports.bot_file_sync_port import (
    BotFileSyncPort,
    BotFileSyncResult,
    BotFileSyncTarget,
)


class BotFileSyncRegistry:
    """Resolve a ``BotFileSyncPort`` per engine type; unknown engines get the noop.

    Only the composition root registers engine-specific implementations. This
    module neither imports the NAS-rsync implementation nor reads its
    configuration — it holds what the root bound, and answers ``resolve`` with
    the default when nothing was bound for an engine.
    """

    def __init__(self, strategies: Mapping[str, BotFileSyncPort]) -> None:
        self._strategies = dict(strategies)
        self._default = NoopBotFileSyncPort()

    def resolve(self, engine_type: str | None) -> BotFileSyncPort:
        if not engine_type:
            return self._default
        return self._strategies.get(engine_type, self._default)


class DelegatingBotFileSyncPort(BotFileSyncPort):
    """The one port the materialiser calls: resolves by dst engine and delegates.

    Holding the registry rather than a single strategy is what lets the DI bind
    one ``file_sync`` into both the ARCA and teclaw port bundles and have the
    answer be engine-correct on each: an ARCA (openclaw) dst resolves to the
    NAS strategy, a teclaw dst resolves to the noop refusal (the capabilities
    resolver already refuses BOT_FILE_SYNC on teclaw at ``PUT``, so this is the
    belt-and-braces second refusal the apply path also honours).
    """

    def __init__(self, registry: BotFileSyncRegistry) -> None:
        self._registry = registry

    async def sync(
        self,
        *,
        target: BotFileSyncTarget,
        source_bot_id: str,
        source_owner_id: str,
        path: str,
        excludes: tuple[str, ...],
    ) -> BotFileSyncResult:
        strategy = self._registry.resolve(target.engine_type)
        return await strategy.sync(
            target=target,
            source_bot_id=source_bot_id,
            source_owner_id=source_owner_id,
            path=path,
            excludes=excludes,
        )


__all__ = ["BotFileSyncRegistry", "DelegatingBotFileSyncPort"]
