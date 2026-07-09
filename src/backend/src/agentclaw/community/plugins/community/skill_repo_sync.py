"""Community ``SkillRepoSyncPlugin`` — host-side local skills directory.

A real, deployable impl (not a ``MockSeam`` test double). Community skills live in
a local directory on the host (mounted/populated by the operator); this plugin
owns the community skills **source location** and the skill-path policy. The skills
root is configurable via ``AGENTCLAW_SKILLS_ROOT``
(default ``~/.openclaw/workspace/skills``).

De-vendoring boundary (B6): this impl depends on **no** corp infra — no git, no
Mist/PAT, no AntGroup OSS. All of that lives in the corp
``ProdSkillRepoSyncPlugin`` → ``GitSyncService`` behind the plugin seam and is
never reachable on the community profile. ``sync()`` reports on the local skills
dir and ``get_local_skills_root()`` exposes it, so a **co-located** community
engine reads skills straight from that local path — no ObjectStorage round-trip.

TODO(totalfrank): FUTURE FEATURE (not a de-vendoring gap) — if a community
deployment ever runs the engine in a *remote* runtime that can't see the host
disk, add a community-native delivery (local dir → community ``ObjectStoragePlugin``
tar/meta + ``ac_skill`` metadata) plus a matching engine read-path. That spans
backend **and** ``src/engine``, so it needs its own SDD; it is out of scope for the
backend-only device/ARCA de-vendoring.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin

logger = get_logger()

_SKILLS_ROOT_ENV = "AGENTCLAW_SKILLS_ROOT"


class CommunitySkillRepoSync(SkillRepoSyncPlugin):
    """Local-directory skills source for the community profile."""

    async def sync(self) -> dict[str, Any]:
        """Report on the host-side ``skills-repo`` source dir.

        ``fetch`` is ``True`` iff the dir exists and holds at least one skill
        subdir. A missing source is not a failure (community deploys may not have
        skills yet) — ``success`` stays ``True`` and any error is captured.
        """
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
                        subtrees["skills"] = {"updated": True, "entries": children}
            return {
                "success": True,
                "fetch": fetched,
                "subtrees": subtrees,
                "error": None,
            }
        except OSError as exc:
            logger.warning("[CommunitySkillRepoSync] sync() error: %s", exc)
            return {
                "success": True,
                "fetch": False,
                "subtrees": {},
                "error": str(exc),
            }

    def get_local_skills_root(self) -> Path | None:
        """The host-side skills root holding ``skills-repo`` (and ``skills-local``).

        Configurable via ``AGENTCLAW_SKILLS_ROOT``; defaults to
        ``~/.openclaw/workspace/skills``.
        """
        override = os.getenv(_SKILLS_ROOT_ENV)
        if override:
            return Path(override).expanduser()
        return Path.home() / ".openclaw" / "workspace" / "skills"

    def get_scan_target(self, default_fallback: Path) -> Path:
        """No atomic cloud skills_target in community — return the caller's
        fallback (the local market repo dir)."""
        return default_fallback

    def get_data_init_skill_md_path(self) -> str:
        """Prefer ``$DATA_INIT_SKILL_MD_PATH``, else the skills-root data-init
        SKILL.md if present, else a conventional path under the skills root."""
        env_path = os.getenv("DATA_INIT_SKILL_MD_PATH")
        if env_path and Path(env_path).exists():
            return env_path
        root = self.get_local_skills_root() or Path.home()
        candidate = root / "skills-repo" / "infra" / "data-init" / "SKILL.md"
        return str(candidate)
