"""Default, ordinary Set and Direct ownership guards."""

from __future__ import annotations
import pytest
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.mcp import (
    BotMCPInstallation,
    SkillSetMCPServer,
)
from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)

from .capability_desired_state_support import (
    _Database,
)


@pytest.mark.parametrize("excluded", [False, True])
def test_a_default_skill_member_requires_exclusion_to_join_an_ordinary_set(excluded):
    """An exclusion releases the Default source only for this Bot's Set addition."""
    db = _Database()
    with db.transactional_orm_session() as session:
        default_set = SkillSet(
            name="defaults",
            user_id="",
            bolt_id="",
            engine_type="openclaw",
            is_default=True,
            is_active=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="mine",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        member = Skill(name="member", git_path="git://defaults/member", env="dev")
        excluded_skill = Skill(
            name="excluded", git_path="git://defaults/excluded", env="dev"
        )
        session.add_all([default_set, ordinary, member, excluded_skill])
        session.flush()
        session.add_all([
            SkillSetSkill(skill_set_id=default_set.id, skill_id=member.id, env="dev"),
            SkillSetSkill(skill_set_id=default_set.id, skill_id=excluded_skill.id, env="dev"),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=member.id, env="dev"
            ),
        ])
        if excluded:
            session.add(DefaultSkillsetSkillExclusion(
                user_id="owner", bot_id="bot",
                skill_set_id=int(default_set.id), skill_id=int(excluded_skill.id),
            ))
        else:
            session.add(BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=excluded_skill.id, env="dev"
            ))

    repository = CapabilityDesiredStateRepository(db)

    with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"):
        repository.add_skill(
            bot_id="bot", owner_id="owner", set_id=str(ordinary.id),
            skill_id=str(member.id), engine_type="openclaw",
            default_engine_types=("openclaw",),
        )
    if excluded:
        assert repository.add_skill(
            bot_id="bot", owner_id="owner", set_id=str(ordinary.id),
            skill_id=str(excluded_skill.id), engine_type="openclaw",
            default_engine_types=("openclaw",),
        ).changed
    else:
        with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"):
            repository.add_skill(
                bot_id="bot", owner_id="owner", set_id=str(ordinary.id),
                skill_id=str(excluded_skill.id), engine_type="openclaw",
                default_engine_types=("openclaw",),
            )


def test_direct_active_precedes_membership_when_both_forbid_joining():
    """R2 before R3 — today's error precedence, now encoded in the policy."""
    db = _Database()
    with db.transactional_orm_session() as session:
        other = SkillSet(
            name="other",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        target = SkillSet(
            name="target",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        skill = Skill(name="both", git_path="git://team/both", env="dev")
        session.add_all([other, target, skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=other.id, skill_id=skill.id, env="dev"),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
                ),
            ]
        )

    with pytest.raises(SkillSetControlPlaneConflictError) as error:
        CapabilityDesiredStateRepository(db).add_skill(
            bot_id="bot",
            owner_id="owner",
            set_id=str(target.id),
            skill_id="1",
            engine_type="openclaw",
        )
    assert "RESOURCE_DIRECT_ACTIVE" in str(error.value)


@pytest.mark.parametrize("excluded", [False, True])
def test_a_default_set_mcp_member_requires_exclusion_to_join_an_ordinary_set(excluded):
    """An excluded Default membership releases only the MCP add-to-Set path."""
    db = _Database()
    with db.transactional_orm_session() as session:
        default_set = SkillSet(
            name="defaults",
            user_id="",
            bolt_id="",
            engine_type="openclaw",
            is_default=True,
            is_active=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="mine",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        session.add_all([default_set, ordinary])
        session.flush()
        session.add(
            SkillSetMCPServer(
                skill_set_id=default_set.id,
                server_code="mcp.default-member",
                name="mcp.default-member",
                env="dev",
            )
        )
        if excluded:
            session.add(DefaultSkillsetMcpExclusion(
                user_id="owner", bot_id="bot", skill_set_id=default_set.id,
                server_code="mcp.default-member",
            ))
        else:
            session.add(BotMCPInstallation(
                bot_id="bot", owner_id="owner",
                server_code="mcp.default-member", env="dev",
            ))

    def add_member():
        return CapabilityDesiredStateRepository(db).add_mcp(
            platform_default_codes=frozenset(),
            bot_id="bot",
            owner_id="owner",
            set_id=str(ordinary.id),
            server_code="mcp.default-member",
            name="mcp.default-member",
            description=None,
            icon=None,
            engine_type="openclaw",
            default_engine_types=("openclaw",),
        )
    if excluded:
        assert add_member().changed is True
    else:
        with pytest.raises(SkillSetControlPlaneConflictError) as error:
            add_member()
        assert "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET" in str(error.value)


def test_direct_skill_installation_mirrors_the_mcp_pair():
    """install/uninstall_skill carry the exact contract of the MCP twin."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        skill = Skill(name="tool", git_path="git://tool", env="dev")
        skill_set = SkillSet(
            name="set", user_id="owner", bolt_id="bot", is_active=False, env="dev"
        )
        session.add_all([skill, skill_set])
        session.flush()

    assert repository.install_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    ).changed
    assert not repository.install_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_DIRECT_ACTIVE"
    ):
        repository.add_skill(
            bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
            skill_id=str(skill.id),
        )
    assert repository.uninstall_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    ).changed
    assert not repository.uninstall_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    ).changed
    assert repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"
    ):
        repository.install_skill(
            bot_id="bot", owner_id="owner", skill_id=str(skill.id)
        )
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"
    ):
        repository.uninstall_skill(
            bot_id="bot", owner_id="owner", skill_id=str(skill.id)
        )


def test_an_excluded_default_member_allows_direct_control_for_skills_and_mcps():
    """An actual scoped exclusion releases Default ownership for Direct commands."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        skill = Skill(name="member", git_path="git://member", env="dev")
        session.add_all([default, skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=default.id, skill_id=skill.id, env="dev"
                ),
                SkillSetMCPServer(
                    skill_set_id=default.id, server_code="mcp.member",
                    name="member", env="dev",
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner", bot_id="bot", skill_set_id=default.id,
                    skill_id=skill.id,
                ),
                DefaultSkillsetMcpExclusion(
                    user_id="owner", bot_id="bot", skill_set_id=default.id,
                    server_code="mcp.member",
                ),
            ]
        )

    scope = dict(engine_type="openclaw", default_engine_types=("openclaw",))
    for command, address in [
        (repository.install_skill, dict(skill_id=str(skill.id))),
        (repository.uninstall_skill, dict(skill_id=str(skill.id))),
        (
            repository.install_mcp,
            dict(server_code="mcp.member", platform_default_codes=frozenset()),
        ),
        (
            repository.uninstall_mcp,
            dict(server_code="mcp.member", platform_default_codes=frozenset()),
        ),
    ]:
        command(bot_id="bot", owner_id="owner", **scope, **address)


def test_direct_skill_installation_validates_existence_and_name_uniqueness():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        active = Skill(name="dup", git_path="git://active", env="dev")
        rival = Skill(name="dup", git_path="git://rival", env="dev")
        foreign = Skill(
            name="foreign", git_path="local://foreign", bolt_id="another-bot",
            user_id="someone", env="dev",
        )
        session.add_all([active, rival, foreign])
        session.flush()

    assert repository.install_skill(
        bot_id="bot", owner_id="owner", skill_id=str(active.id)
    ).changed
    with pytest.raises(SkillRuntimeNameConflictError):
        repository.install_skill(
            bot_id="bot", owner_id="owner", skill_id=str(rival.id)
        )
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.install_skill(bot_id="bot", owner_id="owner", skill_id="999999")
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.install_skill(
            bot_id="bot", owner_id="owner", skill_id=str(foreign.id)
        )
