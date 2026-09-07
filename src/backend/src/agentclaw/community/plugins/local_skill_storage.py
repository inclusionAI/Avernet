"""Default Local Skill storage: preserve the configured per-Bot address policy."""

from pathlib import Path

from agentclaw.community.plugin_api.local_skill_storage import LocalSkillStorageResolver


class ConfiguredLocalSkillStorage(LocalSkillStorageResolver):
    def resolve_root(self, runtime_engine: str, configured_root: Path) -> Path:
        return configured_root
