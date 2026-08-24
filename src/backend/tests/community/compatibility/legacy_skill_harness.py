"""Reusable Q7 fixture factory and release-report renderer.

This module is test infrastructure, not a new runtime routing layer.  It
models the four independent content sources as filesystem fixtures so a future
Center rollout can prove coexistence without seeding historical state.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from agentclaw.community.kernel.bot_config import (
    SCHEMA_VERSION,
    BotConfigArtifact,
    SkillRef,
    StoreRef,
)


BotType = Literal["personal", "desktop", "service"]
Result = Literal["passed", "failed", "blocked", "not run"]

_SKILL_MD = "---\nname: regression-skill\ndescription: Q7 isolated fixture\n---\n# Regression Skill\n"


@dataclass(frozen=True, slots=True)
class LegacyCompatibilityCase:
    """One supported Bot Type × logical-engine delivery shape from Spec §13."""

    bot_type: BotType
    engine: str
    image: str

    @property
    def id(self) -> str:
        return f"{self.bot_type}-{self.engine}-{self.image}"

    @property
    def is_teclaw_v4(self) -> bool:
        return self.engine == "teclaw"


LEGACY_COMPATIBILITY_MATRIX: tuple[LegacyCompatibilityCase, ...] = (
    LegacyCompatibilityCase("personal", "openclaw", "native"),
    LegacyCompatibilityCase("personal", "claude_code", "native"),
    LegacyCompatibilityCase("personal", "claude_code", "aicoding"),
    LegacyCompatibilityCase("personal", "hermes", "native"),
    LegacyCompatibilityCase("personal", "teclaw", "v4"),
    LegacyCompatibilityCase("desktop", "openclaw", "native"),
    LegacyCompatibilityCase("desktop", "hermes", "native"),
    LegacyCompatibilityCase("service", "openclaw", "native"),
    LegacyCompatibilityCase("service", "claude_code", "native"),
    LegacyCompatibilityCase("service", "claude_code", "aicoding"),
    LegacyCompatibilityCase("service", "teclaw", "v4"),
)


@dataclass(frozen=True, slots=True)
class LegacySkillFixture:
    """Fresh, self-contained content fixture for one supported delivery shape."""

    case: LegacyCompatibilityCase
    root: Path
    repo_skill: Path
    local_skill: Path
    bot_local_skill: Path
    center_skill: Path
    repo_locator: str
    local_locator: str
    bot_local_locator: str
    active_links: Mapping[str, Path]
    teclaw_v4_artifact: BotConfigArtifact | None

    def assert_legacy_baseline(self) -> None:
        """Assert coexistence without allowing source or locator conversion."""

        for skill_md in (
            self.repo_skill,
            self.local_skill,
            self.bot_local_skill,
            self.center_skill,
        ):
            assert skill_md.read_text() == _SKILL_MD

        assert self.repo_locator == "git://regression/repo-skill"
        assert self.local_locator == f"local://{self.local_skill.parent}"
        assert self.bot_local_locator == f"local://{self.bot_local_skill.parent}"
        assert not any(
            locator.startswith("center://")
            for locator in (self.repo_locator, self.local_locator, self.bot_local_locator)
        )

        for name, link in self.active_links.items():
            assert link.is_symlink(), f"missing active link: {name}"

        if self.teclaw_v4_artifact is not None:
            assert self.teclaw_v4_artifact.to_dict() == {
                "schema_version": 4,
                "engine_type": "teclaw",
                "mcp": {"servers": []},
                "stores": {
                    "skills-repo": {
                        "type": "oss", "bucket": None, "base": "legacy/skills-repo",
                        "endpoint": None, "region": None,
                    },
                    "bot-data": {
                        "type": "oss", "bucket": None, "base": "legacy/bot-data",
                        "endpoint": None, "region": None,
                    },
                },
                "skills": [
                    {"name": "repo-skill", "scope": "shared", "store": "skills-repo", "path": "regression/repo-skill"},
                    {"name": "local-skill", "scope": "user", "store": "bot-data", "path": "skills-local/local-skill"},
                    {"name": "bot-local-skill", "scope": "user", "store": "bot-data", "path": "skills-local/bot-local-skill"},
                ],
                "resources": [],
                "identity_files": [],
                "engine_overrides": {},
                "engine_ext": {},
                "version": 7,
            }


class LegacySkillFixtureFactory:
    """Creates disposable fixtures with no dependency on an existing Bot or DB."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def create(self, case: LegacyCompatibilityCase) -> LegacySkillFixture:
        root = self._workspace / case.id
        repo_skill = root / "skills-repo" / "regression" / "repo-skill" / "SKILL.md"
        local_skill = root / "skills-local" / "local-skill" / "SKILL.md"
        bot_local_skill = root / "bot-local" / "bot-local-skill" / "SKILL.md"
        center_skill = root / "skill-center" / "center-skill-uuid" / "1.0.0" / "SKILL.md"
        for skill_md in (repo_skill, local_skill, bot_local_skill, center_skill):
            skill_md.parent.mkdir(parents=True, exist_ok=True)
            skill_md.write_text(_SKILL_MD)

        active_links: dict[str, Path] = {}
        if not case.is_teclaw_v4:
            active = root / "active-skills"
            active.mkdir()
            for name, source in (
                ("repo-skill", repo_skill.parent),
                ("local-skill", local_skill.parent),
                ("bot-local-skill", bot_local_skill.parent),
            ):
                link = active / name
                link.symlink_to(source)
                active_links[name] = link

        artifact: BotConfigArtifact | None = None
        if case.is_teclaw_v4:
            artifact = BotConfigArtifact(
                schema_version=SCHEMA_VERSION,
                engine_type="teclaw",
                version=7,
                stores={
                    "skills-repo": StoreRef(type="oss", base="legacy/skills-repo"),
                    "bot-data": StoreRef(type="oss", base="legacy/bot-data"),
                },
                skills=[
                    SkillRef("repo-skill", "shared", "skills-repo", "regression/repo-skill"),
                    SkillRef("local-skill", "user", "bot-data", "skills-local/local-skill"),
                    SkillRef("bot-local-skill", "user", "bot-data", "skills-local/bot-local-skill"),
                ],
            )

        return LegacySkillFixture(
            case=case,
            root=root,
            repo_skill=repo_skill,
            local_skill=local_skill,
            bot_local_skill=bot_local_skill,
            center_skill=center_skill,
            repo_locator="git://regression/repo-skill",
            local_locator=f"local://{local_skill.parent}",
            bot_local_locator=f"local://{bot_local_skill.parent}",
            active_links=active_links,
            teclaw_v4_artifact=artifact,
        )


def render_release_report(
    *, results: Mapping[str, Result], blocked: Mapping[str, str]
) -> str:
    """Render the Q7 standard release gate report from matrix evidence.

    An omitted matrix cell is deliberately a blocker: a release cannot pass on
    the strength of whichever cases happened to run.
    """

    lines = [
        "# Q7 Legacy Skill Compatibility 发布报告",
        "",
        "| Matrix | 结果 | 阻断原因 |",
        "| --- | --- | --- |",
    ]
    release_blocked = False
    for case in LEGACY_COMPATIBILITY_MATRIX:
        result = results.get(case.id, "not run")
        reason = blocked.get(case.id, "")
        if result != "passed" or reason:
            release_blocked = True
        lines.append(f"| {case.id} | {result} | {reason} |")

    conclusion = "阻断" if release_blocked else "通过"
    lines.extend(
        [
            "",
            f"发布结论：{conclusion}",
            "",
            "兼容性声明：Legacy Local、Repo、Bot-local 未自动转换；"
            "Teclaw v4 Legacy Store/Skill 引用保持不变。",
        ]
    )
    return "\n".join(lines)
