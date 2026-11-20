"""Service API for Local Skill create-or-replace uploads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class LocalSkillUploadServiceProtocol(Protocol):
    """Create or safely replace one Bot-owned Local Skill from a complete package."""

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]: ...

    async def ensure_dst_local_skill_asset(
        self, *, bot_id: str, owner_id: str, actor_id: str,
        source_bot_id: str, source_owner_id: str, name: str,
    ) -> dict[str, Any]:
        """Register the dst ``ac_skill`` row for a bot-file-synced Local Skill.

        See
        :meth:`LocalSkillUploadService.ensure_dst_local_skill_asset` for the
        authoritative contract. Reverse-looks-up the source bot's
        ``ac_skill`` row for ``name``/``description``, recomputes the dst
        ``git_path`` through the same factory the upload road takes, and
        writes the dst row (create-or-replace same-name owned) under the same
        edit guard the upload road uses — without calling
        ``LocalSkillPackageRuntime`` (file-sync already wrote the package).
        """
        ...

    async def upload_local_skill_files(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        files: Sequence[tuple[str, bytes]],
    ) -> dict[str, Any]:
        """Create or replace from one browser-selected directory's files.

        Each tuple is ``(relative_path, content)``.  The Service API owns the
        conversion to the same validated package contract as raw ZIP upload.
        """
        ...

    async def installed_package_digest(
        self,
        *,
        bot: Mapping[str, Any],
        bot_id: str,
        owner_id: str,
        name: str,
    ) -> Optional[str]:
        """The installed package's canonical digest, or ``None`` if absent.

        Reads the bytes actually published under the skill's stable
        layout-owned locator — the same read ``verify``/``copy_to`` perform —
        and answers the sha256 of their canonical repack, so a caller holding
        the same package can ask "is what is installed *this* content?"
        without any storage knowledge of its own. ``None`` when no package is
        installed under the name, or the installed package cannot be read
        back: unreadable is *unknown*, not *equal* — callers should class the
        entry for a full write rather than guess.
        """
        ...

    async def export_installed_package(
        self, *, bot: Mapping[str, Any], bot_id: str, owner_id: str, name: str
    ) -> bytes:
        """Read and validate the complete installed package; failures propagate."""
        ...
