"""Network-isolated local implementation of the Space Skill source boundary."""

from agentclaw.community.plugins.local._mock_seam import MockSeam
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.space_skill_source import (
    ExactSkillPackageFetchError,
    GitSkillSnapshot,
    GitSnapshotError,
    SpaceSkillSourcePlugin,
)


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.STUB,
    rationale="test profile never reaches Git or external exact-download URLs",
)
class LocalSpaceSkillSource(MockSeam, SpaceSkillSourcePlugin):
    def fetch_git_snapshot(
        self, *, git_url: str, branch: str | None, subdir: str | None
    ) -> GitSkillSnapshot:
        raise GitSnapshotError("Git snapshots are unavailable in the local stub")

    def fetch_exact_package(self, *, url: str, expected_sha256: str) -> bytes:
        raise ExactSkillPackageFetchError(
            "exact Skill package downloads are unavailable in the local stub"
        )
