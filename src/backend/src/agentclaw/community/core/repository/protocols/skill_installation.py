"""Persistence contract for the active-only Bot Skill Installation fact."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class SkillInstallationRepositoryProtocol(Protocol):
    """Persist and query a Bot's desired active Skill set."""

    @abstractmethod
    def install(
        self, *, env: str, owner_id: str, bot_id: str, skill_id: str | int
    ) -> bool:
        """Create the active row; return false if it already existed."""

    @abstractmethod
    def uninstall(
        self, *, env: str, owner_id: str, bot_id: str, skill_id: str | int
    ) -> bool:
        """Delete the active row; return false if it was already absent."""

    @abstractmethod
    def list_installed_skill_ids(
        self, *, env: str, owner_id: str, bot_id: str
    ) -> set[int]:
        """Return only active desired-state Skill ids for one Bot."""
