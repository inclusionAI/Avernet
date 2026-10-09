"""Per-bot configuration manifest repository contract (issue #1469).

Every member is ``@abstractmethod``: an implementation that omits one fails at
construction naming the missing member, instead of raising ``AttributeError`` at
the call site. Domain imports are ``TYPE_CHECKING``-only — see
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.repository.models import (
        BotConfigManifestRecord,
    )


@runtime_checkable
class BotConfigManifestRepositoryProtocol(Protocol):
    """配置清单仓库接口.

    Keyed on ``(env, entity_id, bot_id)`` and backed by a UNIQUE constraint on
    ``ac_bot_config_manifest``. A bot has at most one manifest; clearing it
    deletes the row, so "no row" and "no manifest" are the same state and every
    read returns ``None`` rather than an empty document.

    That key names one bot for the life of the data, so a lookup needs no
    ownership check on top of it: ``ac_bots`` carries
    ``uk_bot_id_entity_id_env`` and deletion there is a soft update, so a
    deleted bot goes on occupying the tuple and no later bot can take it. See
    the DDL in ``core/bot_config_manifest/sql/`` for what depends on that.

    The document is stored and returned **verbatim**. Validation happens above
    this layer; persistence does not normalise, re-serialise or trim what it was
    handed, because ``script.body`` inside the document is a shell body whose
    bytes are its meaning.
    """

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestRecord]:
        """Return the stored manifest for ``(env, entity_id, bot_id)``, or ``None``.

        ``None`` means the bot has never had a manifest, or its manifest was
        cleared — the caller must not treat the absence as an error.
        """
        ...

    @abstractmethod
    def upsert(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        document: str,
        size_bytes: int,
        schema_version: int,
        modifier: str,
    ) -> BotConfigManifestRecord:
        """Insert the manifest, or replace an existing row's document.

        ``modifier`` is the acting user resolved from the request principal; it
        is never supplied by the client. Returns the stored record, whose
        ``gmt_modified`` is the server's own timestamp.
        """
        ...

    @abstractmethod
    def delete(self, *, env: str, entity_id: str, bot_id: str) -> bool:
        """Hard-delete the row. Idempotent — returns ``False`` when absent."""
        ...
