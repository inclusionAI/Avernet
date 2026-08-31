"""Bot config manifest document repository contract (issue #1469, W1).

Every member is ``@abstractmethod``: an implementation that omits one fails at
construction naming the missing member, instead of raising ``AttributeError``
at the call site. Domain imports are ``TYPE_CHECKING``-only — see
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
    """配置清单文档仓储接口.

    Keyed on ``(env, entity_id, bot_id)`` — the same logical key
    ``ac_bot_startup_script`` uses, and backed by the same reasoning:
    ``ac_bots`` carries ``uk_bot_id_entity_id_env`` and deletion there is a
    soft update, so one key names one bot for the life of the data and no
    later bot can inherit the row. A bot has at most one manifest document;
    ``_delete`` exists for "the caller declares nothing" (which is a distinct
    state from ``[]``, see the DDL comment).

    Storage is fidelity, not normalization: ``document`` round-trips
    byte-exact so the script body inside it survives to execution unchanged.
    """

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestRecord]:
        """Return the stored document for ``(env, entity_id, bot_id)`` or ``None``.

        ``None`` means the bot has never declared a manifest, or the
        declaration was removed — the service turns that into an empty
        document, never an error.
        """
        ...

    @abstractmethod
    def upsert(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        schema_version: int,
        document: str,
        size_bytes: int,
        modifier: str,
    ) -> BotConfigManifestRecord:
        """Insert the document, or whole-replace an existing row.

        ``document`` is the service's canonical serialization of the parsed
        document — it is stored as given: the repository must not re-serialize,
        reformat or re-quote it (JSON string values, the script body above all,
        must round-trip byte-exact). ``modifier`` is the acting user resolved
        from the request principal, never supplied by the client.
        """
        ...

    @abstractmethod
    def delete(self, *, env: str, entity_id: str, bot_id: str) -> bool:
        """Hard-delete the declaration row. Idempotent — ``False`` when absent.

        Deletes the *declaration* only. Materialized entities and managed
        markers are unaffected; that boundary is the D2 semantics and lives
        in the apply layer (W4), not here.
        """
        ...
