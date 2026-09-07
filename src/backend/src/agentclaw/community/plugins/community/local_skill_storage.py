"""Remote community Local uploads use the runtime's existing source layout.

The shared SkillRepoSync source remains a Backend-local repository. It cannot
supply the private upload root for the Claude Code runtime.
"""

from pathlib import Path

from agentclaw.community.core.workspace.skill_layout import pool_paths_for_engine
from agentclaw.community.plugin_api.local_skill_storage import LocalSkillStorageResolver


class EngineLocalSkillStorage(LocalSkillStorageResolver):
    def resolve_root(self, runtime_engine: str, configured_root: Path) -> Path:
        if runtime_engine == "claude_code":
            return Path(pool_paths_for_engine(runtime_engine).legacy_local)
        return configured_root
