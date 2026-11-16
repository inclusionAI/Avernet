"""Skill mutation projection scopes include MCP dependencies."""

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
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)

from .capability_desired_state_support import (
    _Database,
    _DEFAULT_SCOPE,
    _seed_skill_with_dependencies,
    _seed_center_skill_with_dependencies,
)


def test_add_skill_reports_the_skill_s_mcp_dependencies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(
        db, '["mcp.weather", {"server_code": "mcp.maps"}]'
    )

    result = repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    # Both stored shapes decode, through the same decoder the projection uses.
    assert result.mcp_codes == frozenset({"mcp.weather", "mcp.maps"})


def test_add_center_skill_persists_uuid_and_later_activation_installs_it():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        skill = Skill(
            name="center",
            git_path="center://public-center",
            skill_uuid="stable-center-uuid",
            version=1,
            status="PUBLISHED",
            env="dev",
        )
        skill_set = SkillSet(
            name="set",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        session.add_all([skill, skill_set])
        session.flush()
        session.add(SkillVersion(
            skill_id=skill.id, publication_attempt_id=None, version_ordinal=1,
            status="PUBLISHED", sc_version_number="v1.0",
            sc_skill_id=1001, sc_version_id=2001,
            name=skill.name, description="Center version",
            metadata_json='{"mcp_dependencies":[]}',
            published_at=datetime(2026, 9, 1), created_by="owner", env="dev",
        ))
        skill_id = str(skill.id)
        set_id = str(skill_set.id)

    repository.add_skill(
        bot_id="bot",
        owner_id="owner",
        set_id=set_id,
        skill_id=skill_id,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        membership = session.query(SkillSetSkill).one()
        assert membership.skill_uuid == "stable-center-uuid"

    repository.set_skill_set_active(
        bot_id="bot",
        owner_id="owner",
        set_id=set_id,
        active=True,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert {
            installation.skill_id
            for installation in session.query(BotSkillInstallation).all()
        } == {int(skill_id)}


def test_add_skill_reports_no_dependencies_when_the_skill_declares_none():
    """What lets a dependency-free Skill mutation skip the MCP projection."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, None)

    result = repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert result.mcp_codes == frozenset()


def test_skill_set_toggle_reports_member_skill_mcp_dependencies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, '["mcp.weather"]')
    repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )
    with db.transactional_orm_session() as session:
        session.add(SkillSetMCPServer(
            skill_set_id=skill_set.id, server_code="mcp.direct",
            name="Direct MCP", env="dev",
        ))

    deactivated = repository.set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), active=False,
    )
    activated = repository.set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), active=True,
    )

    assert deactivated.mcp_codes == frozenset({"mcp.weather", "mcp.direct"})
    assert activated.mcp_codes == frozenset({"mcp.weather", "mcp.direct"})
    with db.orm_session() as session:
        assert {row.server_code for row in session.query(BotMCPInstallation)} == {
            "mcp.direct"
        }


def test_skill_set_deactivation_preserves_dependency_mcp_override():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, '["mcp.weather"]')
    repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )
    with db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id="bot", owner_id="owner", server_code="mcp.weather",
            config='{"url":"https://weather.example/mcp"}', env="dev",
        ))

    result = repository.set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), active=False,
    )

    assert result.mcp_codes == frozenset({"mcp.weather"})
    with db.orm_session() as session:
        assert session.query(BotMCPInstallation).count() == 0
        assert session.query(BotMCPConfig).one().server_code == "mcp.weather"


def test_set_deactivation_with_unreadable_center_dependencies_keeps_cleanup_possible():
    db = _Database()
    skill, skill_set = _seed_center_skill_with_dependencies(db, "{malformed")
    with db.transactional_orm_session() as session:
        session.add_all([
            SkillSetSkill(
                skill_set_id=skill_set.id, skill_id=skill.id,
                skill_uuid=skill.skill_uuid, env="dev",
            ),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev",
            ),
        ])

    result = CapabilityDesiredStateRepository(db).set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), active=False,
    )

    assert result.changed is True
    assert result.mcp_scope_unknown is True
    with db.orm_session() as session:
        assert session.get(SkillSet, skill_set.id).is_active is False
        assert session.query(BotSkillInstallation).count() == 0


def test_set_deactivation_marks_unresolved_center_member_mcp_scope_unknown():
    db = _Database()
    with db.transactional_orm_session() as session:
        skill = Skill(
            name="old-center", git_path="center://old-center",
            skill_uuid="00000000-0000-4000-8000-000000000125",
            status="OFFLINE", env="dev",
        )
        skill_set = SkillSet(
            name="set", user_id="owner", bolt_id="bot", is_active=True, env="dev",
        )
        session.add_all([skill, skill_set])
        session.flush()
        session.add_all([
            SkillSetSkill(
                skill_set_id=skill_set.id, skill_id=skill.id,
                skill_uuid=skill.skill_uuid, env="dev",
            ),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev",
            ),
        ])

    result = CapabilityDesiredStateRepository(db).set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), active=False,
    )

    assert result.mcp_scope_unknown is True
    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_set_toggle_uses_latest_published_center_version_dependencies():
    db = _Database()
    skill, skill_set = _seed_center_skill_with_dependencies(
        db, '{"mcp_dependencies":["mcp.old"]}',
    )
    with db.transactional_orm_session() as session:
        session.add_all([
            SkillSetSkill(
                skill_set_id=skill_set.id, skill_id=skill.id,
                skill_uuid=skill.skill_uuid, env="dev",
            ),
            SkillVersion(
                skill_id=skill.id, publication_attempt_id=None,
                version_ordinal=2, status="PUBLISHED",
                sc_version_number="v2.0", sc_skill_id=1001, sc_version_id=2002,
                name=skill.name, description="Latest Center version",
                metadata_json='{"mcp_dependencies":["mcp.new"]}',
                published_at=datetime(2026, 9, 2), created_by="owner", env="dev",
            ),
        ])

    repository = CapabilityDesiredStateRepository(db)
    deactivated = repository.set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), active=False,
    )
    activated = repository.set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), active=True,
    )

    assert deactivated.mcp_codes == frozenset({"mcp.new"})
    assert activated.mcp_codes == frozenset({"mcp.new"})


def test_set_activation_rejects_center_version_without_dependency_metadata():
    db = _Database()
    skill, skill_set = _seed_center_skill_with_dependencies(db, "{}")
    with db.transactional_orm_session() as session:
        session.get(SkillSet, skill_set.id).is_active = False
        session.add(SkillSetSkill(
            skill_set_id=skill_set.id, skill_id=skill.id,
            skill_uuid=skill.skill_uuid, env="dev",
        ))

    with pytest.raises(ValueError, match="invalid MCP dependencies"):
        CapabilityDesiredStateRepository(db).set_skill_set_active(
            bot_id="bot", owner_id="owner", set_id=str(skill_set.id), active=True,
        )

    with db.orm_session() as session:
        assert session.get(SkillSet, skill_set.id).is_active is False
        assert session.query(BotSkillInstallation).count() == 0


def test_center_skill_add_and_remove_use_latest_published_version_dependencies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_center_skill_with_dependencies(
        db,
        '{"mcp_dependencies":[{"code":"mcp.old"}]}',
    )
    with db.transactional_orm_session() as session:
        session.add(
            SkillVersion(
                skill_id=skill.id,
                publication_attempt_id=None,
                version_ordinal=2,
                status="PUBLISHED",
                sc_version_number="v2.0",
                sc_skill_id=1001,
                sc_version_id=2002,
                name=skill.name,
                description="Latest Center dependency fixture",
                metadata_json=(
                    '{"mcp_dependencies":[{"code":"mcp.center"}]}'
                ),
                published_at=datetime(2026, 9, 2),
                created_by="owner",
                env="dev",
            )
        )

    added = repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )
    removed = repository.remove_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert added.mcp_codes == frozenset({"mcp.center"})
    assert removed.mcp_codes == frozenset({"mcp.center"})


def test_center_direct_install_and_uninstall_use_version_dependencies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, _skill_set = _seed_center_skill_with_dependencies(
        db,
        '{"mcp_dependencies":[{"code":"mcp.center"}]}',
    )

    installed = repository.install_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    )
    uninstalled = repository.uninstall_skill(
        bot_id="bot", owner_id="owner", skill_id=str(skill.id)
    )

    assert installed.mcp_codes == frozenset({"mcp.center"})
    assert uninstalled.mcp_codes == frozenset({"mcp.center"})


def test_a_no_op_add_skill_claims_nothing():
    """Re-adding an existing member changes no MCP, so it claims none."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, '["mcp.weather"]')
    repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    result = repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert not result.changed
    assert result.mcp_codes == frozenset()


def test_remove_skill_reports_the_dependencies_it_releases():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, '["mcp.weather"]')
    repository.add_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    result = repository.remove_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert result.changed
    assert result.mcp_codes == frozenset({"mcp.weather"})


def test_a_no_op_remove_skill_releases_nothing():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    skill, skill_set = _seed_skill_with_dependencies(db, '["mcp.weather"]')

    result = repository.remove_skill(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        skill_id=str(skill.id),
    )

    assert not result.changed
    assert result.mcp_codes == frozenset()


def test_excluding_a_default_member_releases_its_dependencies():
    """Exclusion is the Default Set's per-Bot deactivation of one member, so
    it moves that member's dependencies exactly as an ordinary remove does."""
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        skill = Skill(
            name="member", git_path="git://member", env="dev",
            mcp_dependencies='["mcp.weather"]',
        )
        session.add_all([default, skill])
        session.flush()
        session.add_all([
            SkillSetSkill(skill_set_id=default.id, skill_id=skill.id, env="dev"),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
            ),
        ])

    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    assert excluded.mcp_codes == frozenset({"mcp.weather"})

    restored = repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    assert restored.mcp_codes == frozenset({"mcp.weather"})


def test_center_default_exclusion_uses_published_version_dependencies():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="default", user_id="", bolt_id="", engine_type="openclaw",
            is_default=True, env="dev",
        )
        skill = Skill(
            name="center-default",
            git_path="center://center-default",
            env="dev",
            status="PUBLISHED",
            skill_uuid="00000000-0000-4000-8000-000000000124",
        )
        session.add_all([default, skill])
        session.flush()
        session.add_all([
            SkillVersion(
                skill_id=skill.id,
                publication_attempt_id=None,
                version_ordinal=1,
                status="PUBLISHED",
                sc_version_number="v1.0",
                sc_skill_id=1002,
                sc_version_id=2002,
                name=skill.name,
                description="Center default fixture",
                metadata_json=(
                    '{"mcp_dependencies":[{"code":"mcp.center-default"}]}'
                ),
                published_at=datetime(2026, 9, 1),
                created_by="owner",
                env="dev",
            ),
            SkillSetSkill(
                skill_set_id=default.id,
                skill_id=skill.id,
                skill_uuid=skill.skill_uuid,
                env="dev",
            ),
            BotSkillInstallation(
                bot_id="bot", owner_id="owner", skill_id=skill.id, env="dev"
            ),
        ])

    excluded = repository.exclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )
    restored = repository.unexclude_default_skill(
        set_id=str(default.id), skill_id=str(skill.id), **_DEFAULT_SCOPE
    )

    assert excluded.mcp_codes == frozenset({"mcp.center-default"})
    assert restored.mcp_codes == frozenset({"mcp.center-default"})
