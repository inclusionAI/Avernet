from pathlib import Path

import pytest

from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    LayoutIdentity,
    LogicalSkillMapping,
    ResolvedFilesystemLayoutPlan,
    RuntimeLayoutContext,
    SkillCorpus,
    SkillLayoutResolutionError,
    UnsupportedRuntimeLayoutError,
    resolve_filesystem_skill_layout,
    resolve_skill_mappings,
)


def _plan(engine: str = "openclaw") -> ResolvedFilesystemLayoutPlan:
    return resolve_filesystem_skill_layout(
        LayoutIdentity(
            engine_type=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
        ),
        RuntimeLayoutContext(home=Path("/runtime")),
    )


def _resolve_pool_mappings(
    plan: ResolvedFilesystemLayoutPlan,
    mappings: list[LogicalSkillMapping],
):
    return resolve_skill_mappings(
        active_root=plan.active_root,
        local_root=plan.pool_local,
        repo_root=plan.pool_repo,
        mappings=mappings,
    )


@pytest.mark.parametrize(
    ("source_layout", "expected_source"),
    [
        (
            "legacy",
            "/runtime/.openclaw/workspace/skills/skills-repo/business/reviewer",
        ),
        (
            "pool",
            (
                "/runtime/.openclaw/workspace/skills-pool/"
                "skills-repo/business/reviewer"
            ),
        ),
    ],
)
def test_logical_mapping_uses_selected_corpus_without_changing_active_target(
    source_layout: str,
    expected_source: str,
) -> None:
    plan = _plan()
    local_root = (
        plan.pool_local if source_layout == "pool" else plan.legacy_local
    )
    repo_root = (
        plan.pool_repo if source_layout == "pool" else plan.legacy_repo
    )

    resolved = resolve_skill_mappings(
        active_root=plan.active_root,
        local_root=local_root,
        repo_root=repo_root,
        mappings=[
            LogicalSkillMapping(
                corpus=SkillCorpus.REPO,
                relative_path="business/reviewer",
                link_name="reviewer",
            )
        ],
    )

    assert resolved[0].source == Path(expected_source)
    assert resolved[0].target == Path(
        "/runtime/.openclaw/workspace/skills/reviewer"
    )
    assert resolved[0].resolved_locator == "git://business/reviewer"


@pytest.mark.parametrize(
    ("engine", "expected_active", "expected_local", "expected_repo"),
    [
        (
            "openclaw",
            ".openclaw/workspace/skills",
            ".openclaw/workspace/skills-pool/skills-local/writer",
            ".openclaw/workspace/skills-pool/skills-repo/business/reviewer",
        ),
        (
            "claude_code",
            ".claude/skills",
            ".claude_code/workspace/skills-pool/skills-local/writer",
            ".claude_code/workspace/skills-pool/skills-repo/business/reviewer",
        ),
        (
            "aicoding",
            ".claude/skills",
            ".aicoding/workspace/skills-pool/skills-local/writer",
            ".aicoding/workspace/skills-pool/skills-repo/business/reviewer",
        ),
        (
            "hermes",
            ".hermes/skills",
            ".hermes/workspace/skills-pool/skills-local/writer",
            ".hermes/workspace/skills-pool/skills-repo/business/reviewer",
        ),
    ],
)
def test_logical_mapping_preserves_four_engine_projection_snapshot(
    engine: str,
    expected_active: str,
    expected_local: str,
    expected_repo: str,
) -> None:
    plan = _plan(engine)

    resolved = _resolve_pool_mappings(
        plan,
        [
            LogicalSkillMapping(SkillCorpus.LOCAL, "writer", "writer"),
            LogicalSkillMapping(
                SkillCorpus.REPO,
                "business/reviewer",
                "reviewer",
            ),
        ],
    )

    assert resolved[0].source.relative_to(Path("/runtime")).as_posix() == (
        expected_local
    )
    assert resolved[1].source.relative_to(Path("/runtime")).as_posix() == (
        expected_repo
    )
    assert resolved[0].target.relative_to(Path("/runtime")).as_posix() == (
        f"{expected_active}/writer"
    )
    assert resolved[1].target.relative_to(Path("/runtime")).as_posix() == (
        f"{expected_active}/reviewer"
    )


@pytest.mark.parametrize(
    ("relative_path", "link_name"),
    [
        (".", "writer"),
        ("../writer", "writer"),
        ("/absolute/writer", "writer"),
        ("business//writer", "writer"),
        ("business/./writer", "writer"),
        (" writer", "writer"),
        ("writer\x00draft", "writer"),
        ("writer", "../writer"),
        ("writer", "nested/writer"),
        ("writer", "/writer"),
        ("writer", "writer\x00draft"),
    ],
)
def test_logical_mapping_rejects_noncanonical_paths(
    relative_path: str,
    link_name: str,
) -> None:
    plan = _plan()

    with pytest.raises(SkillLayoutResolutionError):
        _resolve_pool_mappings(
            plan,
            [
                LogicalSkillMapping(
                    SkillCorpus.LOCAL,
                    relative_path,
                    link_name,
                )
            ],
        )


def test_logical_mapping_rejects_unknown_corpus() -> None:
    plan = _plan()

    with pytest.raises(SkillLayoutResolutionError, match="unknown Skill corpus"):
        _resolve_pool_mappings(
            plan,
            [
                LogicalSkillMapping(
                    "future",  # type: ignore[arg-type]
                    "writer",
                    "writer",
                )
            ],
        )


def test_logical_mapping_rejects_duplicate_active_target() -> None:
    plan = _plan()

    with pytest.raises(
        SkillLayoutResolutionError,
        match="duplicate active Skill target",
    ):
        _resolve_pool_mappings(
            plan,
            [
                LogicalSkillMapping(SkillCorpus.LOCAL, "writer", "same"),
                LogicalSkillMapping(
                    SkillCorpus.REPO,
                    "business/writer",
                    "same",
                ),
            ],
        )


def test_teclaw_artifact_plan_rejects_logical_filesystem_mapping() -> None:
    with pytest.raises(UnsupportedRuntimeLayoutError):
        resolve_filesystem_skill_layout(
            LayoutIdentity(
                engine_type="teclaw",
                layout_contract_version=LAYOUT_CONTRACT_VERSION,
            ),
            RuntimeLayoutContext(home=Path("/runtime")),
        )
