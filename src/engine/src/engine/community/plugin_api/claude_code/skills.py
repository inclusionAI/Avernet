"""ClaudeCodeSkillsPort — native port for skill lifecycle management.

Skills are pooled (client + relay), so port methods take
``token: str | None = None`` for per-token routing. Returns raw
dicts / list[dict] / bool — the adapter builds the core DTOs.

Relay RPC mapping (teamclaw-aicoding-relay v3 protocol):

==========================  ================================================
Port method                 Relay RPC (method name on the wire)
==========================  ================================================
``skills_list``             ``skills.list``
``skills_get``              ``skills.get``
``skills_install``          ``skills.install``
``skills_uninstall``        ``skills.uninstall``
``skills_update``           ``skills.update``
``skills_enable``           ``skills.enable``
``skills_disable``          ``skills.disable``
``skills_execute``          ``skills.execute``
``skills_validate``         ``skills.validate``
``skills_discover``         ``skills.discover``
``skills_sync_symlinks``    ``skills.sync_symlinks``
``skills_sync_bindpaths``   ``skills.sync_bindpaths``
``skills_clean_symlinks``   ``skills.clean_symlinks``
``skills_ensure_center``    ``skills.ensure_center``
==========================  ================================================
"""

from __future__ import annotations

from typing import Protocol


class ClaudeCodeSkillsPort(Protocol):
    """Native skill lifecycle operations over the claude_code gateway (vendored Node relay)."""

    async def skills_list(
        self,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``skills.list``; return raw skill descriptor dicts.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def skills_get(
        self,
        skill_id: str,
        token: str | None = None,
    ) -> dict | None:
        """Call ``skills.get`` for a single skill.

        Returns ``None`` when the skill_id is not present.

        Args:
            skill_id: The skill identifier to look up.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def skills_install(
        self,
        config: dict,
        token: str | None = None,
    ) -> dict:
        """Call ``skills.install`` to install a skill from config.

        Args:
            config: Raw skill install config (name, source, path, ...).
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw installed skill descriptor dict.
        """
        ...

    async def skills_uninstall(
        self,
        skill_id: str,
        token: str | None = None,
    ) -> bool:
        """Call ``skills.uninstall``; return True on success, False on error.

        Args:
            skill_id: The skill identifier to remove.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def skills_update(
        self,
        skill_id: str,
        patch: dict,
        token: str | None = None,
    ) -> dict:
        """Call ``skills.update`` to patch an existing skill.

        Args:
            skill_id: The skill identifier to update.
            patch: Partial config dict to merge.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw skill descriptor dict after update.
        """
        ...

    async def skills_enable(
        self,
        skill_id: str,
        token: str | None = None,
    ) -> bool:
        """Call ``skills.enable``; return True on success, False on error.

        Args:
            skill_id: The skill identifier to enable.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def skills_disable(
        self,
        skill_id: str,
        token: str | None = None,
    ) -> bool:
        """Call ``skills.disable``; return True on success, False on error.

        Args:
            skill_id: The skill identifier to disable.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def skills_execute(
        self,
        skill_id: str,
        args: dict | None = None,
        token: str | None = None,
    ) -> dict:
        """Call ``skills.execute`` to run a skill and return the raw result.

        Args:
            skill_id: The skill identifier to execute.
            args: Optional arguments dict for the skill invocation.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...

    async def skills_validate(
        self,
        config: dict,
        token: str | None = None,
    ) -> dict:
        """Call ``skills.validate`` to validate a skill config without installing.

        Args:
            config: Raw skill config to validate.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw validation result dict.
        """
        ...

    async def skills_discover(
        self,
        source: str,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``skills.discover`` to discover skills from a source.

        Args:
            source: Discovery source identifier (registry, path, url, ...).
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def skills_sync_symlinks(
        self,
        token: str | None = None,
    ) -> dict:
        """Call ``skills.sync_symlinks`` to synchronize skill symlinks.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def skills_sync_bindpaths(
        self,
        token: str | None = None,
    ) -> dict:
        """Call ``skills.sync_bindpaths`` to synchronize skill bindpaths.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def skills_clean_symlinks(
        self,
        token: str | None = None,
    ) -> dict:
        """Call ``skills.clean_symlinks`` to remove stale skill symlinks.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def skills_ensure_center(
        self,
        token: str | None = None,
    ) -> dict:
        """Call ``skills.ensure_center`` to ensure center skills are present.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def activate_pool_layout(
        self,
        params: dict,
    ) -> dict:
        """Atomically switch Claude Code's physical Legacy local to Pool."""
        ...

    async def rollback_pool_layout(
        self,
        params: dict,
    ) -> dict:
        """Atomically rebuild and switch Claude Code back to Legacy local.

        ``params`` requires ``rollback_generation`` (str) and
        ``registered_local_names`` (list[str]). The result requires
        ``committed`` (bool), ``status`` (a Pool activation status string),
        and ``evidence`` (dict). Only ``COMMITTED`` and
        ``ALREADY_COMMITTED`` mean the filesystem authority changed;
        consumers must fail closed for unknown status values.
        """
        ...

    async def probe_pool_layout(
        self,
        params: dict,
    ) -> dict:
        """Inspect Claude Code's marker, canonical roots, and stable bridges."""
        ...

    async def publish_pool_mappings(
        self,
        params: dict,
    ) -> dict:
        """Publish the complete managed mapping set under ``~/.claude/skills``."""
        ...

    async def verify_pool_mappings(
        self,
        params: dict,
    ) -> dict:
        """Verify managed Claude Code entries against Pool sources."""
        ...


__all__ = ["ClaudeCodeSkillsPort"]
