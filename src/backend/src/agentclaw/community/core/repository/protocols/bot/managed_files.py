"""Repository contract for ``ac_bot_config_managed_files`` (W8, #1476).

The index of files the platform delivered to a teclaw bot on a manifest's
behalf. Every member is ``@abstractmethod`` so an implementation missing one
fails at construction rather than at the call site. Domain imports are
``TYPE_CHECKING``-only, per ``core/repository/README.md``.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.repository.managed_files_models import (
        ManagedFileRecord,
    )


@runtime_checkable
class BotConfigManagedFilesRepositoryProtocol(Protocol):
    """One row per platform-delivered file, keyed by bot, category and path."""

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str, category: str, rel_path: str
    ) -> Optional["ManagedFileRecord"]:
        """One file's row, or ``None``."""
        ...

    @abstractmethod
    def upsert(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        category: str,
        name: str,
        rel_path: str,
        store_key: str,
        digest: str,
        size_bytes: int,
        apply_id: Optional[str],
    ) -> "ManagedFileRecord":
        """Insert the row, or replace an existing one for the same path."""
        ...

    @abstractmethod
    def delete(
        self, *, env: str, entity_id: str, bot_id: str, category: str, rel_path: str
    ) -> bool:
        """Remove one file's row. ``False`` when there was none."""
        ...

    @abstractmethod
    def list_by_category(
        self, *, env: str, entity_id: str, bot_id: str, category: str
    ) -> list["ManagedFileRecord"]:
        """Every row of one category, in path order."""
        ...

    @abstractmethod
    def list_all(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> list["ManagedFileRecord"]:
        """Every row of the bot, category then path order."""
        ...
