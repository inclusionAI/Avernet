"""Deterministic exact-version Scanner double for test/singlebox profiles."""

from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionScanResult,
    SkillVersionScannerProtocol,
)
from agentclaw.community.core.skill_center.skill_package import (
    ValidatedSkillPackage,
)


class FakeSkillVersionScanner(SkillVersionScannerProtocol):
    """A successful empty scan; production remains bound to the strict SDK."""

    def scan(self, package: ValidatedSkillPackage) -> SkillVersionScanResult:
        del package
        return SkillVersionScanResult(risk_tags=(), mcp_dependencies=())


__all__ = ["FakeSkillVersionScanner"]
