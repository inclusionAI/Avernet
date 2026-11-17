"""Default exclusion commands and desired-state compensation."""

from __future__ import annotations
from datetime import datetime
import pytest
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.mcp import (
    BotMCPConfig,
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
    SkillOfflineError,
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)

from .capability_desired_state_support import (
    _Database,
    _seed_default_with_member,
    _DEFAULT_SCOPE,
)


def test_exclusion_retires_the_installation_row_in_one_command():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)

    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )

    assert excluded.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1
        assert session.query(BotSkillInstallation).count() == 0
    assert repository.excluded_default_skill_ids(
        bot_id="bot", owner_id="owner", set_id=str(default.id)
    ) == {int(skill.id)}
    # The flush agrees: an excluded member is an inactive claim.
    plan = repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev",
        engine_type="openclaw", default_engine_types=("openclaw",),
    )
    assert int(skill.id) not in plan.skills_to_install
    # Idempotent retry owns neither half.
    assert not repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed


def test_unexclusion_restores_the_installation_row_in_one_command():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )

    restored = repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )

    assert restored.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 0
        assert [
            int(row.skill_id) for row in session.query(BotSkillInstallation).all()
        ] == [int(skill.id)]
    assert not repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed


@pytest.mark.parametrize("active", [False, True])
def test_excluded_default_skill_can_move_to_ordinary_set_without_restoring_default(active):
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(
            name="mine", user_id="owner", bolt_id="bot",
            engine_type="openclaw", is_active=active, env="dev",
        )
        session.add(ordinary)
        session.flush()
        ordinary_id = ordinary.id

    assert repository.add_skill(
        set_id=str(ordinary_id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed
    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).filter_by(
            bot_id="bot", owner_id="owner", skill_id=skill.id
        ).count() == int(active)
        assert session.query(DefaultSkillsetSkillExclusion).filter_by(
            bot_id="bot", user_id="owner", skill_id=skill.id
        ).count() == 1

    assert not repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed
    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).filter_by(
            bot_id="bot", owner_id="owner", skill_id=skill.id
        ).count() == int(active)
    with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"):
        repository.unexclude_default_skill(
            set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
        )
    repository.remove_skill(
        set_id=str(ordinary_id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    assert repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed


def test_another_bots_skill_exclusion_does_not_release_default_membership():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(
            name="mine", user_id="owner", bolt_id="bot",
            engine_type="openclaw", is_active=False, env="dev",
        )
        session.add(ordinary)
        session.flush()
        ordinary_id = ordinary.id
        session.add(DefaultSkillsetSkillExclusion(
            user_id="owner", bot_id="other-bot",
            skill_set_id=default.id, skill_id=skill.id,
        ))

    with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"):
        repository.add_skill(
            set_id=str(ordinary_id), skill_id=str(skill.id), **_DEFAULT_SCOPE
        )


def test_center_skill_unexclusion_rejects_ordinary_membership_with_stale_row_id():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        ordinary = SkillSet(
            name="mine", user_id="owner", bolt_id="bot",
            engine_type="openclaw", is_active=True, env="dev",
        )
        skill = Skill(
            name="center", git_path="center://uuid-a", skill_uuid="uuid-a",
            version=2, status="PUBLISHED", env="dev",
        )
        session.add_all([default, ordinary, skill])
        session.flush()
        session.add_all([
            SkillSetSkill(
                skill_set_id=default.id, skill_id=skill.id,
                skill_uuid="uuid-a", env="dev",
            ),
            SkillSetSkill(
                skill_set_id=ordinary.id, skill_id=999,
                skill_uuid="uuid-a", env="dev",
            ),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev",
            ),
        ])

    assert repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed
    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 1
    with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"):
        repository.unexclude_default_skill(
            set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
        )
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1
        assert session.query(BotSkillInstallation).count() == 1


def test_offline_skill_rejects_membership_direct_and_default_restore_before_writes():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    with db.transactional_orm_session() as session:
        row = session.query(Skill).filter_by(id=skill.id).one()
        row.offline_at = datetime(2026, 8, 30)
        ordinary = SkillSet(
            name="ordinary",
            user_id="owner",
            bolt_id="bot",
            is_active=True,
            env="dev",
        )
        session.add(ordinary)
        session.flush()
        ordinary_id = ordinary.id

    with pytest.raises(SkillOfflineError, match="SKILL_OFFLINE"):
        repository.add_skill(
            bot_id="bot",
            owner_id="owner",
            set_id=str(ordinary_id),
            skill_id=str(skill.id),
        )
    with pytest.raises(SkillOfflineError, match="SKILL_OFFLINE"):
        repository.install_skill(
            bot_id="bot", owner_id="owner", skill_id=str(skill.id)
        )
    with pytest.raises(SkillOfflineError, match="SKILL_OFFLINE"):
        repository.unexclude_default_skill(
            set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
        )

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).filter_by(skill_set_id=ordinary_id).count() == 0
        assert session.query(BotSkillInstallation).count() == 0
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1


def test_unexclusion_fails_closed_on_a_runtime_name_conflict():
    """The name guard aborts the whole command: the exclusion stays."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    with db.transactional_orm_session() as session:
        rival = Skill(name="member", git_path="git://rival", env="dev")
        session.add(rival)
        session.flush()
        session.add(
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=rival.id, env="dev"
            )
        )

    with pytest.raises(SkillRuntimeNameConflictError):
        repository.unexclude_default_skill(
            set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
        )

    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1


def test_mcp_exclusion_mirrors_the_skill_pair():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)

    excluded = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    assert excluded.changed is True
    assert excluded.mcp_codes == frozenset({"mcp.member"})
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 1
        assert session.query(BotMCPInstallation).count() == 0
    assert repository.excluded_default_mcp_codes(
        bot_id="bot", owner_id="owner", set_id=str(default.id)
    ) == {"mcp.member"}
    unchanged_exclusion = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    assert unchanged_exclusion.changed is False
    assert unchanged_exclusion.mcp_codes == frozenset()

    restored = repository.unexclude_default_mcp(
                   platform_default_codes=frozenset(),
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    assert restored.changed is True
    assert restored.mcp_codes == frozenset({"mcp.member"})
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0
        assert [
            row.server_code for row in session.query(BotMCPInstallation).all()
        ] == ["mcp.member"]

    # Once the owner excludes Default again, an active ordinary Set may own
    # the same MCP. Repeating Default exclusion must preserve its state, and
    # un-exclusion must refuse the duplicate source.
    repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(
            name="mine", user_id="owner", bolt_id="bot",
            engine_type="openclaw", is_active=True, env="dev",
        )
        session.add(ordinary)
        session.flush()
    repository.add_mcp(
        set_id=str(ordinary.id), server_code="mcp.member", name="member",
        description=None, icon=None, platform_default_codes=frozenset(),
        **_DEFAULT_SCOPE,
    )
    with db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            owner_id="owner", bot_id="bot", server_code="mcp.member",
            config='{"headers":{}}', env="dev",
        ))
    repeated = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    assert repeated.changed is False
    with db.orm_session() as session:
        assert session.query(BotMCPInstallation).count() == 1
        assert session.query(BotMCPConfig).count() == 1
    with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"):
        repository.unexclude_default_mcp(
            platform_default_codes=frozenset(),
            set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
        )
    repository.remove_mcp(
        set_id=str(ordinary.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    assert repository.unexclude_default_mcp(
               platform_default_codes=frozenset(),
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    ).changed


def test_ordinary_remove_mcp_refuses_a_default_set_address() -> None:
    """Default opt-out has one command; membership removal must not duplicate it."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)

    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SYSTEM_DEFAULT_IMMUTABLE"
    ):
        repository.remove_mcp(
            set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
        )

    assert (
        repository.excluded_default_mcp_codes(
            bot_id="bot", owner_id="owner", set_id=str(default.id)
        )
        == set()
    )


def test_exclusion_commands_refuse_an_ordinary_set_address():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(
            name="ordinary", user_id="owner", bolt_id="bot",
            engine_type="openclaw", env="dev",
        )
        session.add(ordinary)
        session.flush()

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.exclude_default_skill(
            set_id=str(ordinary.id), skill_id="1", **_DEFAULT_SCOPE
        )
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        repository.unexclude_default_mcp(
            platform_default_codes=frozenset(),
            set_id=str(ordinary.id), server_code="mcp.x", **_DEFAULT_SCOPE
        )


def test_excluding_a_never_member_owns_neither_half():
    """No dangling exclusion row, and the wire must not report a change."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)
    with db.transactional_orm_session() as session:
        stranger = Skill(name="stranger", git_path="git://stranger", env="dev")
        session.add(stranger)
        session.flush()

    for skill_id in (str(stranger.id), "424242", "not-a-number"):
        assert not repository.exclude_default_skill(
            set_id=str(default.id), skill_id=skill_id, **_DEFAULT_SCOPE
        ).changed
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 0
        assert session.query(BotSkillInstallation).count() == 1


def test_excluding_an_unresolvable_member_still_retires_its_installation():
    """An OFFLINE center member's row has no other off switch (R1 refuses the
    direct command); the exclusion is the Default Set's removal path."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        offline = Skill(
            name="offline", git_path="center://offline-uuid",
            skill_uuid="offline-uuid", status="OFFLINE", env="dev",
            mcp_dependencies="{malformed",
        )
        session.add_all([default, offline])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=default.id, skill_id=offline.id,
                    skill_uuid="offline-uuid", env="dev",
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=offline.id,
                    env="dev",
                ),
            ]
        )

    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(offline.id), **_DEFAULT_SCOPE
    )

    assert excluded.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1
        assert session.query(BotSkillInstallation).count() == 0


def test_restore_desired_state_compensates_exclusion_commands():
    """The compensation contract: a restore from the command's own snapshot
    undoes both halves, so a failed projection cannot half-apply — and the
    flush cannot re-apply — an exclusion."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)

    # Exclude, then compensate as the mutation flow would.
    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    assert excluded.previous_state.skill_exclusions == frozenset()
    repository.restore_desired_state(
        bot_id="bot", owner_id="owner", state=excluded.previous_state,
        engine_type="openclaw",
    )
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 0
        assert session.query(BotSkillInstallation).count() == 1
    plan = repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev",
        engine_type="openclaw", default_engine_types=("openclaw",),
    )
    assert int(skill.id) in plan.skills_to_install

    # Un-exclude, then compensate: the exclusion row must come back.
    repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    restored = repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    assert restored.previous_state.skill_exclusions == frozenset(
        {(int(default.id), int(skill.id))}
    )
    repository.restore_desired_state(
        bot_id="bot", owner_id="owner", state=restored.previous_state,
        engine_type="openclaw",
    )
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetSkillExclusion).count() == 1
        assert session.query(BotSkillInstallation).count() == 0
    # And the retry the user reaches for after a failed command now works.
    assert repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    ).changed


def test_compensation_cannot_restore_reference_after_offline_wins():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, skill = _seed_default_with_member(db)
    previous = repository.snapshot_desired_state(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )
    with db.transactional_orm_session() as session:
        session.query(BotSkillInstallation).filter_by(skill_id=skill.id).delete()
        persisted = session.query(Skill).filter_by(id=skill.id).one()
        persisted.offline_at = datetime(2026, 8, 30, 12, 0)
        persisted.offline_by = "owner"

    with pytest.raises(SkillOfflineError, match="SKILL_OFFLINE"):
        repository.restore_desired_state(
            bot_id="bot",
            owner_id="owner",
            state=previous,
            engine_type="openclaw",
        )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_excluding_a_stray_mcp_code_owns_neither_half():
    """The MCP twin of the skill never-member gate: no dangling row.

    A code that is neither an association row nor a platform default is a
    stray — a typo or stale UI state. Writing its exclusion anyway would
    pre-exclude the server for this Bot if the platform ever adds it to the
    shared Default Set.
    """
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)

    refused = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.stray", **_DEFAULT_SCOPE
    )

    assert refused.changed is False
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0
        assert session.query(BotMCPInstallation).count() == 1


def test_excluding_a_platform_default_mcp_retires_a_legacy_direct_row():
    """Policy exclusion converges rows written before Direct control was banned.

    Engine/template default MCPs are policy, not association rows, so the
    caller names them; excluding one writes the exclusion row — that row is
    exactly how a Bot opts out of a platform default. Any Installation row for
    the same code is a legacy Direct-control artifact and must be removed or it
    would immediately bypass the exclusion through the installed union half.
    """
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)
    with db.transactional_orm_session() as session:
        session.add(
            BotMCPInstallation(
                bot_id="bot", owner_id="owner",
                server_code="mcp.platform", env="dev",
            )
        )

    excluded = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.platform",
        platform_default_codes=frozenset({"mcp.platform"}), **_DEFAULT_SCOPE
    )

    assert excluded.changed is True
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 1
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"mcp.member"}
    assert repository.excluded_default_mcp_codes(
        bot_id="bot", owner_id="owner", set_id=str(default.id)
    ) == {"mcp.platform"}

    # Idempotent exclusion is also a repair point for a row written by an old
    # process racing this deployment.
    with db.transactional_orm_session() as session:
        session.add(
            BotMCPInstallation(
                bot_id="bot", owner_id="owner",
                server_code="mcp.platform", env="dev",
            )
        )
    repaired = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.platform",
        platform_default_codes=frozenset({"mcp.platform"}), **_DEFAULT_SCOPE
    )
    assert repaired.changed is True
    with db.orm_session() as session:
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"mcp.member"}

    restored = repository.unexclude_default_mcp(
                   platform_default_codes=frozenset({"mcp.platform"}),
        set_id=str(default.id), server_code="mcp.platform", **_DEFAULT_SCOPE
    )
    assert restored.changed is True
    assert restored.mcp_codes == frozenset({"mcp.platform"})


def test_restore_desired_state_preserves_mcp_membership_metadata():
    """Compensation recreates the association row, not a husk of it.

    A failed projection triggers restore for every ordinary Set the Bot has;
    losing name/description/icon there would turn a transient runtime
    failure into permanent metadata corruption on memberships the mutation
    never touched.
    """
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(
            name="tools", user_id="owner", bolt_id="bot",
            engine_type="openclaw", is_active=True, env="dev",
        )
        session.add(ordinary)
        session.flush()
        session.add(
            SkillSetMCPServer(
                skill_set_id=ordinary.id, server_code="mcp.rich",
                name="Rich MCP", description="does rich things",
                icon="https://icons.example/rich.png", user_id="owner",
                env="dev",
            )
        )

    removed = repository.remove_mcp(
        set_id=str(ordinary.id), server_code="mcp.rich", **_DEFAULT_SCOPE
    )
    assert removed.changed is True
    repository.restore_desired_state(
        bot_id="bot", owner_id="owner", state=removed.previous_state,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        row = session.query(SkillSetMCPServer).one()
        assert (row.server_code, row.name, row.description, row.icon, row.user_id) == (
            "mcp.rich",
            "Rich MCP",
            "does rich things",
            "https://icons.example/rich.png",
            "owner",
        )


def test_restore_desired_state_compensates_mcp_exclusion_commands():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    default, _skill = _seed_default_with_member(db)

    excluded = repository.exclude_default_mcp(
        set_id=str(default.id), server_code="mcp.member", **_DEFAULT_SCOPE
    )
    repository.restore_desired_state(
        bot_id="bot", owner_id="owner", state=excluded.previous_state,
        engine_type="openclaw",
    )
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0
        assert [
            row.server_code for row in session.query(BotMCPInstallation).all()
        ] == ["mcp.member"]
