from __future__ import annotations

from pathlib import Path

import pytest

from engine.community.core.skills.layout_planner import RuntimeLayoutContext
from engine.community.core.skills.local_package_application import (
    LocalSkillPackageApplication,
)
from engine.community.core.skills.models import (
    LocalSkillPackageAction,
    LocalSkillPackageApplyRequest,
    LocalSkillPackageApplyResult,
    LocalSkillPackageLayout,
)


class _Publisher:
    def __init__(self) -> None:
        self.targets: list[Path] = []

    def publish(self, *, skill_name: str, package: bytes, target: Path):
        self.targets.append(target)
        return LocalSkillPackageApplyResult(
            skill_name=skill_name,
            action=LocalSkillPackageAction.CREATED,
            content_digest="sha256:" + "a" * 64,
            target_path=str(target),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_type", "layout", "relative_root"),
    [
        (
            "openclaw",
            LocalSkillPackageLayout.LEGACY,
            ".openclaw/workspace/skills/skills-local",
        ),
        (
            "openclaw",
            LocalSkillPackageLayout.POOL,
            ".openclaw/workspace/skills-pool/skills-local",
        ),
        (
            "claude_code",
            LocalSkillPackageLayout.LEGACY,
            ".claude_code/workspace/skills/skills-local",
        ),
        (
            "claude_code",
            LocalSkillPackageLayout.POOL,
            ".claude_code/workspace/skills-pool/skills-local",
        ),
        (
            "aicoding",
            LocalSkillPackageLayout.LEGACY,
            ".aicoding/workspace/skills/skills-local",
        ),
        (
            "aicoding",
            LocalSkillPackageLayout.POOL,
            ".aicoding/workspace/skills-pool/skills-local",
        ),
        (
            "hermes",
            LocalSkillPackageLayout.LEGACY,
            ".hermes/workspace/skills/skills-local",
        ),
        (
            "hermes",
            LocalSkillPackageLayout.POOL,
            ".hermes/workspace/skills-pool/skills-local",
        ),
    ],
)
async def test_application_projects_only_from_layout_planner(
    tmp_path: Path,
    engine_type: str,
    layout: LocalSkillPackageLayout,
    relative_root: str,
) -> None:
    publisher = _Publisher()
    application = LocalSkillPackageApplication(
        engine_type,
        publisher=publisher,
        context=RuntimeLayoutContext(home=tmp_path),
    )

    await application.apply(
        LocalSkillPackageApplyRequest(
            skill_name="weather",
            layout=layout,
            package=b"zip",
        )
    )

    assert publisher.targets == [tmp_path / relative_root / "weather"]
