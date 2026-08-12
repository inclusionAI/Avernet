"""Per-bot startup script repository contract (issue #926).

Every member is ``@abstractmethod``: an implementation that omits one fails at
construction naming the missing member, instead of raising ``AttributeError`` at
the call site. Domain imports are ``TYPE_CHECKING``-only — see
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_startup_script.repository.models import (
        BotStartupScriptRecord,
    )


@runtime_checkable
class BotStartupScriptRepositoryProtocol(Protocol):
    """启动脚本仓库接口.

    Keyed on ``(env, entity_id, bot_id)`` and backed by a UNIQUE constraint on
    ``ac_bot_startup_script``. A bot has at most one script; clearing it deletes
    the row, so "no row" and "no script" are the same state and every read
    returns ``None`` rather than an empty body.
    """

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotStartupScriptRecord]:
        """Return the stored script for ``(env, entity_id, bot_id)``, or ``None``.

        ``None`` means the bot has never had a script, or its script was
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
        script: str,
        size_bytes: int,
        modifier: str,
    ) -> BotStartupScriptRecord:
        """Insert the script, or replace the body of an existing row.

        ``modifier`` is the acting user resolved from the request principal; it
        is never supplied by the client. Returns the stored record, whose
        ``gmt_modified`` is the server's own timestamp.
        """
        ...

    @abstractmethod
    def delete(self, *, env: str, entity_id: str, bot_id: str) -> bool:
        """Hard-delete the row. Idempotent — returns ``False`` when absent."""
        ...
