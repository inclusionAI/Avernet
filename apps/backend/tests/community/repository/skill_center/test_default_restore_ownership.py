"""Restoring actual Default supply cannot take over Direct ownership."""

import pytest
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)

from tests.community.repository.skill_center.capability_desired_state_support import (
    _Database,
)

SCOPE = dict(
    bot_id="bot",
    owner_id="owner",
    engine_type="openclaw",
    default_engine_types=("openclaw",),
)


def _excluded_default(kind):
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add(Skill(id=1, name="tool", git_path="git://tool", env="dev"))
        session.add(
            SkillSet(
                id=10,
                name="default",
                is_default=True,
                is_active=True,
                user_id="",
                bolt_id="",
                engine_type="openclaw",
                env="dev",
            )
        )
        if kind == "skill":
            session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
        elif kind == "mcp":
            session.add(
                SkillSetMCPServer(
                    skill_set_id=10,
                    server_code="mcp.tool",
                    name="tool",
                    env="dev",
                )
            )
    repo = CapabilityDesiredStateRepository(db)
    if kind == "skill":
        repo.exclude_default_skill(**SCOPE, set_id="10", skill_id="1")
    else:
        repo.exclude_default_mcp(
            **SCOPE,
            set_id="10",
            server_code="mcp.tool",
            platform_default_codes=frozenset({"mcp.tool"}),
        )
    return db, repo


def _claim_direct(repo, kind):
    if kind == "skill":
        repo.install_skill(**SCOPE, skill_id="1")
    else:
        repo.install_mcp(
            **SCOPE,
            server_code="mcp.tool",
            platform_default_codes=frozenset({"mcp.tool"})
            if kind == "policy"
            else frozenset(),
        )


def _restore(repo, kind):
    if kind == "skill":
        return repo.unexclude_default_skill(**SCOPE, set_id="10", skill_id="1")
    return repo.unexclude_default_mcp(
        **SCOPE,
        set_id="10",
        server_code="mcp.tool",
        platform_default_codes=frozenset({"mcp.tool"})
        if kind == "policy"
        else frozenset(),
    )


@pytest.mark.parametrize("kind", ["skill", "mcp", "policy"])
def test_default_restore_refuses_current_direct_and_keeps_exclusion(kind):
    _db, repo = _excluded_default(kind)
    _claim_direct(repo, kind)
    before = repo.snapshot_desired_state(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_DIRECT_ACTIVE"
    ):
        _restore(repo, kind)
    assert (
        repo.snapshot_desired_state(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
        == before
    )


@pytest.mark.parametrize("kind", ["skill", "mcp"])
def test_stale_default_exclusion_can_be_cleared_without_taking_direct_supply(kind):
    db, repo = _excluded_default(kind)
    with db.transactional_orm_session() as session:
        model = SkillSetSkill if kind == "skill" else SkillSetMCPServer
        session.query(model).filter_by(skill_set_id=10).delete()
    _claim_direct(repo, kind)
    restored = _restore(repo, kind)
    assert restored.changed and not restored.mcp_codes
    after = repo.snapshot_desired_state(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )
    if kind == "skill":
        assert after.installations == {1}
        assert not after.skill_exclusions
    else:
        assert after.mcp_installations == {"mcp.tool"}
        assert not after.mcp_exclusions
