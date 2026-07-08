"""Protocol for synchronizing the skill repository (git-based or other)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class SkillRepoSyncPlugin(Plugin, Protocol):
    """Abstracts skill repository synchronization.

    Skill center calls sync() to pull latest skill definitions from
    the upstream repository, without knowing whether the backend is
    git-based, OSS-based, or a no-op stub.
    """

    async def sync(self) -> dict[str, Any]:
        """Execute a full repository sync.

        Returns:
            Dict with keys:
                - success (bool): Whether sync succeeded.
                - fetch (bool): Whether new data was fetched.
                - subtrees (dict): Per-subtree sync results.
                - error (str | None): Error message if failed.
        """
        ...

    def get_local_skills_root(self) -> Path | None:
        """Return the host-side skills root that owns ``skills-local`` and
        ``skills-repo`` sibling dirs, or ``None`` if the bot's skills live
        in a per-bot cloud OSS-view path instead.

        Used by ``WorkspacePathFactory.get_bot_skills_{local,repo}_dir`` so
        the factory itself doesn't have to check runtime mode.

        Strictness varies by impl:

        - **Local (dev/SQLite)**: returns ``~/.openclaw/workspace/skills``.
          Both ``skills-local`` and ``skills-repo`` are siblings under that
          root on the developer's host.
        - **Prod (cloud)**: returns ``None``. Skills paths are computed
          per-bot from entity / bot IDs by ``WorkspacePathFactory`` itself.

        Returns:
            Host skills root, or ``None`` for per-bot cloud paths.
        """
        ...

    def get_scan_target(self, default_fallback: Path) -> Path:
        """Return the path skill-center should scan for skill files.

        The sync plugin knows where its output lives — this method
        exposes that knowledge so callers don't have to reach inside
        the sync service's config.

        Strictness varies by impl:

        - **Prod (git-based)**: returns ``GitSyncService.config.skills_target``
          when it exists. Raises ``RuntimeError`` if missing — refuses
          to fall back to the NAS path because concurrent rsyncs can
          create transient partial-delete races (see incident
          2026-05-13: 32 skill rows wrongly deleted).
        - **Local (noop sync)**: returns ``default_fallback`` directly.
          No NAS race surface; lenient fallback is safe.

        Args:
            default_fallback: Path to use when the sync plugin has no
                authoritative target (local mode), or as the caller's
                "if everything else fails" anchor. Callers usually pass
                the market repo dir.

        Returns:
            Path the caller should scan.

        Raises:
            RuntimeError: Strict impl, when no valid scan target exists.
        """
        ...

    def get_data_init_skill_md_path(self) -> str:
        """Return the engine-view path to the data-init ``SKILL.md`` file.

        Used by ``DataInitService`` when prompting the LLM to read the
        data-init skill instructions. Each impl knows its own layout:

        - **Local**: ``$DATA_INIT_SKILL_MD_PATH`` if set and exists, else
          ``~/.agents/skills/data-init/SKILL.md`` if it exists, else falls
          back to the canonical engine-VM path.
        - **Prod**: the canonical engine-VM path
          ``/home/admin/.openclaw/workspace/skills/skills-repo/infra/data-init/SKILL.md``.

        Returns:
            Absolute path string the LLM should read on the device.
        """
        ...
