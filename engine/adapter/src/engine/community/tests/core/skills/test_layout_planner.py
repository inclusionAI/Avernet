from pathlib import Path

import pytest

from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    LayoutIdentity,
    ResolvedArtifactLayoutPlan,
    ResolvedFilesystemLayoutPlan,
    RuntimeLayoutContext,
    SkillLayoutResolutionError,
    UnsupportedLayoutContractError,
    resolve_skill_layout,
)

HOME = Path("/runtime")


def _resolve(engine: str):
    return resolve_skill_layout(
        LayoutIdentity(
            engine_type=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
        ),
        RuntimeLayoutContext(home=HOME),
    )


def test_resolver_identity_returns_both_layouts_without_selecting_authority() -> None:
    plan = resolve_skill_layout(
        LayoutIdentity(
            engine_type="aicoding",
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
        ),
        RuntimeLayoutContext(home=HOME),
    )

    assert isinstance(plan, ResolvedFilesystemLayoutPlan)
    assert plan.active_root == HOME / ".claude/skills"
    assert plan.legacy_local == HOME / ".aicoding/workspace/skills/skills-local"
    assert plan.pool_local == HOME / ".aicoding/workspace/skills-pool/skills-local"
    assert not hasattr(plan, "layout_state")
    assert not hasattr(plan, "repo_delivery")
    assert not hasattr(plan, "local_root")
    assert not hasattr(plan, "repo_root")


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        (
            "openclaw",
            (
                ".openclaw/workspace/skills",
                ".openclaw/workspace/skills/skills-local",
                ".openclaw/workspace/skills/skills-repo",
                ".openclaw/workspace/skills-pool",
                ".openclaw/workspace/skills/skills-local",
                ".openclaw/workspace/skills/skills-repo",
            ),
        ),
        (
            "claude_code",
            (
                ".claude/skills",
                ".claude_code/workspace/skills/skills-local",
                ".claude_code/skills-repo",
                ".claude_code/workspace/skills-pool",
                ".claude/skills/skills-local",
                ".claude/skills/skills-repo",
            ),
        ),
        (
            "aicoding",
            (
                ".claude/skills",
                ".aicoding/workspace/skills/skills-local",
                ".aicoding/skills-repo",
                ".aicoding/workspace/skills-pool",
                ".claude/skills/skills-local",
                ".aicoding/skills-repo",
            ),
        ),
        (
            "hermes",
            (
                ".hermes/skills",
                ".hermes/workspace/skills/skills-local",
                ".hermes/skills-repo",
                ".hermes/workspace/skills-pool",
                ".hermes/skills/skills-local",
                ".hermes/skills-repo",
            ),
        ),
    ],
)
def test_filesystem_descriptor_snapshot(
    engine: str,
    expected: tuple[str, str, str, str, str, str],
) -> None:
    plan = _resolve(engine)
    assert isinstance(plan, ResolvedFilesystemLayoutPlan)
    assert (
        str(plan.active_root.relative_to(HOME)),
        str(plan.legacy_local.relative_to(HOME)),
        str(plan.legacy_repo.relative_to(HOME)),
        str(plan.pool_root.relative_to(HOME)),
        str(plan.local_bridge.relative_to(HOME)),
        str(plan.repo_bridge.relative_to(HOME)),
    ) == expected
    assert plan.ready_marker == plan.pool_root / ".pool-ready"
    assert plan.marker == plan.ready_marker
    assert plan.active_marker == plan.pool_root / ".pool-active"


def test_teclaw_is_explicit_artifact_layout() -> None:
    plan = _resolve("teclaw")
    assert isinstance(plan, ResolvedArtifactLayoutPlan)
    assert plan.capability.value == "artifact"


def test_unknown_engine_never_falls_back_to_openclaw() -> None:
    with pytest.raises(SkillLayoutResolutionError):
        _resolve("future_engine")


def test_unknown_contract_fails_closed() -> None:
    with pytest.raises(UnsupportedLayoutContractError):
        resolve_skill_layout(
            LayoutIdentity(
                engine_type="openclaw",
                layout_contract_version="skills-pool-future-v2",
            ),
            RuntimeLayoutContext(home=HOME),
        )
