"""Test/singlebox must materialize exact versions without a vendor SDK."""

from injector import Injector

from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionScannerProtocol,
)
from agentclaw.community.core.skill_center.skill_package import (
    ValidatedSkillPackage,
)
from agentclaw.community.di.modules.skill_version_module import SkillVersionModule
from agentclaw.community.di.modules.testing_skill_center_module import (
    TestingSkillCenterModule,
)
from agentclaw.community.testing.skill_version_scanner import (
    FakeSkillVersionScanner,
)


def test_testing_profile_overrides_the_unavailable_scanner_sdk() -> None:
    injector = Injector([SkillVersionModule(), TestingSkillCenterModule()])

    scanner = injector.get(SkillVersionScannerProtocol)
    result = scanner.scan(
        ValidatedSkillPackage(
            name="pdf",
            description="PDF tools",
            files=(("SKILL.md", b"content"),),
            canonical_zip=b"zip",
        )
    )

    assert isinstance(scanner, FakeSkillVersionScanner)
    assert result.risk_tags == ()
    assert result.mcp_dependencies == ()
