"""Local SkillRepoSyncPlugin — simulates fetch from a host-side skills dir."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam

logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.SIMULATOR,
    rationale="sync() simulates a real fetch from local skills-repo dir",
)
class LocalSkillRepoSyncPlugin(MockSeam, SkillRepoSyncPlugin):
    """Offline-friendly sync for local/SQLite mode.

    Instead of a hardcoded no-op, ``sync()`` inspects the host-side
    ``skills-repo`` source under :meth:`get_local_skills_root` and
    reports a real ``fetch`` boolean — ``True`` iff that dir exists and
    holds at least one skill subdir. Unlike prod, a missing source is
    *not* a failure: ``success`` stays ``True`` and any exception is
    captured in ``error`` so offline dev never breaks on a bad path.
    """

    async def sync(self) -> dict[str, Any]:
        """Simulate a fetch from the local ``skills-repo`` source dir."""
        root = self.get_local_skills_root()
        subtrees: dict[str, Any] = {}
        fetched = False
        try:
            if root is not None:
                repo_dir = root / "skills-repo"
                if repo_dir.is_dir():
                    children = sorted(
                        p.name for p in repo_dir.iterdir() if p.is_dir()
                    )
                    if children:
                        fetched = True
                        subtrees["skills"] = {
                            "updated": True,
                            "entries": children,
                        }
            logger.debug(
                "[LocalSkillRepoSync] sync() -> fetch=%s subtrees=%s",
                fetched,
                subtrees,
            )
            return {
                "success": True,
                "fetch": fetched,
                "subtrees": subtrees,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 — offline-friendly capture
            logger.debug("[LocalSkillRepoSync] sync() error: %s", exc)
            return {
                "success": True,
                "fetch": False,
                "subtrees": {},
                "error": str(exc),
            }

    def get_local_skills_root(self) -> Path | None:
        """Local boots own a single host-side skills root.

        ``~/.openclaw/workspace/skills`` holds ``skills-local`` and
        ``skills-repo`` as siblings; ``WorkspacePathFactory`` joins the
        per-call leaf onto this root.
        """
        return Path.home() / ".openclaw" / "workspace" / "skills"

    def get_scan_target(self, default_fallback: Path) -> Path:
        """Local mode has no atomic skills_target; return the caller's
        fallback (typically the market repo dir). Lenient on purpose:
        local has no NAS race surface, so the strict cloud behavior
        (raise) would be unhelpful for offline dev.
        """
        logger.info(
            "[LocalSkillRepoSync.get_scan_target] scan_target=%s (fallback, local mode)",
            default_fallback,
        )
        return default_fallback

    def get_data_init_skill_md_path(self) -> str:
        """Prefer ``$DATA_INIT_SKILL_MD_PATH``, else ``~/.agents/skills/data-init/SKILL.md``
        if it exists; otherwise fall back to the canonical engine-VM path
        (engine may still have the file mounted)."""
        env_path = os.getenv("DATA_INIT_SKILL_MD_PATH")
        if env_path and Path(env_path).exists():
            return env_path
        local_path = Path.home() / ".agents" / "skills" / "data-init" / "SKILL.md"
        if local_path.exists():
            return str(local_path)
        return "/home/admin/.openclaw/workspace/skills/skills-repo/infra/data-init/SKILL.md"
