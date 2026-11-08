"""Consumer-visible bulk SkillSet replacement and Default snapshot behavior."""

from __future__ import annotations

import pytest

from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope,
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)
from .test_capability_desired_state_uow import _Database


SCOPE = BotCapabilityScope("bot", "owner", "openclaw", ("openclaw",), "teamclaw", "dev")


def test_bulk_replacement_reuses_name_and_reports_actual_noop():
    repo = CapabilityDesiredStateRepository(_Database())
    first = repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(ResolvedSkillSetWrite("expert", "works"),),
        remove_names=(),
        default_change=DefaultExclusionChange(),
        platform_default_codes=frozenset(),
    )
    repeated = repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(ResolvedSkillSetWrite("expert", "works"),),
        remove_names=(),
        default_change=DefaultExclusionChange(),
        platform_default_codes=frozenset(),
    )
    assert first.details["created"] == ("expert",)
    assert repeated.changed is False
    assert repeated.details["unchanged"] == ("expert",)
    assert (
        len(repo.list_sets(bot_id="bot", owner_id="owner", engine_type="openclaw")) == 1
    )


def test_bulk_members_exclusions_and_installations_commit_and_rollback_together():
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
    from agentclaw.community.core.models.mcp import SkillSetMCPServer

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(id=1, name="default-skill", git_path="git://one", env="dev"),
                Skill(id=2, name="expert-skill", git_path="git://two", env="dev"),
                SkillSet(
                    id=10,
                    name="default",
                    is_default=True,
                    user_id="",
                    bolt_id="",
                    engine_type="openclaw",
                    env="dev",
                ),
            ]
        )
        session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
        session.add(
            SkillSetMCPServer(
                skill_set_id=10, server_code="default.mcp", name="Default", env="dev"
            )
        )
    change = DefaultExclusionChange(
        add_skills=frozenset({(10, 1)}),
        add_mcps=frozenset({(10, "default.mcp"), (10, "policy.mcp")}),
    )
    result = repo.replace_apply_skill_sets(
        mcp_catalog_entries={
            "expert.mcp": {"name": "expert.mcp", "description": None, "icon": None}
        },
        scope=SCOPE,
        sets=(
            ResolvedSkillSetWrite("expert", skill_ids=(2,), mcp_codes=("expert.mcp",)),
        ),
        remove_names=(),
        default_change=change,
        platform_default_codes=frozenset({"policy.mcp"}),
    )
    after = repo.snapshot_desired_state(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )
    assert result.changed and after.installations == {2}
    assert after.skill_exclusions == frozenset({(10, 1)})
    assert after.mcp_installations == {"expert.mcp"}
    with pytest.raises(Exception):
        repo.replace_apply_skill_sets(
            mcp_catalog_entries={},
            scope=SCOPE,
            sets=(ResolvedSkillSetWrite("expert", skill_ids=(9999,)),),
            remove_names=(),
            default_change=DefaultExclusionChange(remove_skills=frozenset({(10, 1)})),
            platform_default_codes=frozenset({"policy.mcp"}),
        )
    assert (
        repo.snapshot_desired_state(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
        == after
    )


def test_read_apply_snapshot_keeps_excluded_raw_references_without_flushing():
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(
                    id=1,
                    name="gone",
                    git_path="local://gone",
                    user_id="owner",
                    bolt_id="bot",
                    env="dev",
                ),
                SkillSet(
                    id=10,
                    name="default",
                    is_default=True,
                    user_id="",
                    bolt_id="",
                    engine_type="openclaw",
                    env="dev",
                ),
            ]
        )
        session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
    repo.exclude_default_skill(
        bot_id="bot",
        owner_id="owner",
        set_id="10",
        skill_id="1",
        engine_type="openclaw",
        default_engine_types=("openclaw",),
    )
    snapshot = repo.read_apply_snapshot(
        scope=SCOPE, platform_default_codes=frozenset({"policy"})
    )
    assert snapshot.raw_member_skill_ids == frozenset({1})
    assert snapshot.installed_skill_ids == frozenset()
    assert snapshot.platform_default_codes == frozenset({"policy"})
    assert snapshot.skills[0]["name"] == "gone"


@pytest.mark.parametrize("operation", ["create", "rename"])
def test_default_name_is_reserved_for_management(operation):
    repo = CapabilityDesiredStateRepository(_Database())
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SKILL_SET_NAME_RESERVED"
    ):
        if operation == "create":
            repo.create_set(
                bot_id="bot",
                owner_id="owner",
                name="default",
                description=None,
                engine_type="openclaw",
            )
        else:
            item = repo.create_set(
                bot_id="bot",
                owner_id="owner",
                name="expert",
                description=None,
                engine_type="openclaw",
            )
            repo.update_set(
                bot_id="bot",
                owner_id="owner",
                set_id=item["id"],
                name="default",
                description=None,
                engine_type="openclaw",
            )


def test_excluded_default_skill_and_policy_mcp_allow_explicit_direct_control():
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(id=1, name="tool", git_path="git://tool", env="dev"),
                SkillSet(
                    id=10,
                    name="default",
                    is_default=True,
                    user_id="",
                    bolt_id="",
                    engine_type="openclaw",
                    env="dev",
                ),
            ]
        )
        session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
    repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(),
        remove_names=(),
        default_change=DefaultExclusionChange(
            add_skills=frozenset({(10, 1)}), add_mcps=frozenset({(10, "policy")})
        ),
        platform_default_codes=frozenset({"policy"}),
    )
    repo.install_skill(
        bot_id="bot",
        owner_id="owner",
        skill_id="1",
        engine_type="openclaw",
        default_engine_types=("openclaw",),
    )
    repo.install_mcp(
        bot_id="bot",
        owner_id="owner",
        server_code="policy",
        platform_default_codes=frozenset({"policy"}),
        engine_type="openclaw",
        default_engine_types=("openclaw",),
    )
    assert repo.snapshot_desired_state(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    ).installations == {1}
    assert repo.list_installed_mcps(bot_id="bot", owner_id="owner") == {"policy"}


def test_delete_is_by_planned_name_in_current_scope_not_previous_id():
    repo = CapabilityDesiredStateRepository(_Database())
    first = repo.create_set(
        bot_id="bot",
        owner_id="owner",
        name="expert",
        description=None,
        engine_type="openclaw",
    )
    repo.update_set(
        bot_id="bot",
        owner_id="owner",
        set_id=first["id"],
        name="renamed",
        description=None,
        engine_type="openclaw",
    )
    repo.create_set(
        bot_id="bot",
        owner_id="owner",
        name="expert",
        description=None,
        engine_type="openclaw",
    )
    repo.create_set(
        bot_id="bot",
        owner_id="owner",
        name="new",
        description=None,
        engine_type="openclaw",
    )
    result = repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(),
        remove_names=("expert",),
        default_change=DefaultExclusionChange(),
        platform_default_codes=frozenset(),
    )
    assert result.details["removed"] == ("expert",)
    assert {
        item["name"]
        for item in repo.list_sets(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
    } == {"renamed", "new"}


def test_default_snapshot_cleanup_handles_unavailable_raw_members_and_preserves_overrides():
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
    from agentclaw.community.core.models.mcp import BotMCPConfig

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(
                    id=1,
                    name="gone",
                    git_path="center://gone",
                    skill_uuid=None,
                    env="dev",
                ),
                SkillSet(
                    id=10,
                    name="default",
                    is_default=True,
                    user_id="",
                    bolt_id="",
                    engine_type="openclaw",
                    env="dev",
                ),
            ]
        )
        session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
        session.add(
            BotMCPConfig(
                owner_id="owner",
                bot_id="bot",
                server_code="policy",
                config='{"headers":{"X-Test":"preserved"}}',
                env="dev",
            )
        )
    repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(),
        remove_names=(),
        default_change=DefaultExclusionChange(
            add_skills=frozenset({(10, 1)}), add_mcps=frozenset({(10, "policy")})
        ),
        platform_default_codes=frozenset({"policy"}),
    )
    assert repo.get_mcp_overrides(bot_id="bot", owner_id="owner") == {
        "policy": {"headers": {"X-Test": "preserved"}}
    }
    assert repo.read_apply_snapshot(
        scope=SCOPE, platform_default_codes=frozenset({"policy"})
    ).skill_exclusions == frozenset({(10, 1)})


def test_storage_failure_rolls_back_exclusion_and_members_as_one_unit():
    from sqlalchemy import event
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(id=1, name="one", git_path="git://one", env="dev"),
                Skill(id=2, name="two", git_path="git://two", env="dev"),
                SkillSet(
                    id=10,
                    name="default",
                    is_default=True,
                    user_id="",
                    bolt_id="",
                    engine_type="openclaw",
                    env="dev",
                ),
            ]
        )
        session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
    before = repo.snapshot_desired_state(
        bot_id="bot", owner_id="owner", engine_type="openclaw"
    )

    def reject_members(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO ac_skill_set_skill "):
            raise RuntimeError("injected storage failure")

    event.listen(db.engine, "before_cursor_execute", reject_members)
    with pytest.raises(RuntimeError, match="injected storage failure"):
        repo.replace_apply_skill_sets(
            mcp_catalog_entries={},
            scope=SCOPE,
            sets=(ResolvedSkillSetWrite("expert", skill_ids=(2,)),),
            remove_names=(),
            default_change=DefaultExclusionChange(add_skills=frozenset({(10, 1)})),
            platform_default_codes=frozenset(),
        )
    event.remove(db.engine, "before_cursor_execute", reject_members)
    assert (
        repo.snapshot_desired_state(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
        == before
    )
    assert {
        item["name"]
        for item in repo.list_sets(
            bot_id="bot", owner_id="owner", engine_type="openclaw"
        )
    } == {"default"}


def test_current_direct_same_runtime_name_blocks_new_set_even_if_later_direct_removal_planned():
    from agentclaw.community.core.models.skill import Skill
    from agentclaw.community.core.skill_center.errors import (
        SkillRuntimeNameConflictError,
    )

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(id=1, name="same", git_path="git://one", env="dev"),
                Skill(id=2, name="same", git_path="git://two", env="dev"),
            ]
        )
    repo.install_skill(
        bot_id="bot", owner_id="owner", skill_id="1", engine_type="openclaw"
    )
    with pytest.raises(SkillRuntimeNameConflictError):
        repo.replace_apply_skill_sets(
            mcp_catalog_entries={},
            scope=SCOPE,
            sets=(ResolvedSkillSetWrite("expert", skill_ids=(2,)),),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
        )
    assert repo.list_sets(bot_id="bot", owner_id="owner", engine_type="openclaw") == []


def test_raw_local_references_include_inactive_set_from_another_engine():
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(
                    id=1,
                    name="tool",
                    git_path="local://tool",
                    user_id="owner",
                    bolt_id="bot",
                    env="dev",
                ),
                SkillSet(
                    id=10,
                    name="old-engine",
                    user_id="owner",
                    bolt_id="bot",
                    engine_type="hermes",
                    is_active=False,
                    env="dev",
                ),
            ]
        )
        session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
    snapshot = repo.read_apply_snapshot(scope=SCOPE, platform_default_codes=frozenset())
    assert snapshot.sets == ()
    assert snapshot.raw_member_skill_ids == frozenset({1})


def test_default_restore_rechecks_direct_ownership_before_omitted_set_deletion():
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(id=1, name="tool", git_path="git://tool", env="dev"),
                SkillSet(
                    id=10,
                    name="default",
                    is_default=True,
                    user_id="",
                    bolt_id="",
                    engine_type="openclaw",
                    env="dev",
                ),
            ]
        )
        session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
    repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(),
        remove_names=(),
        default_change=DefaultExclusionChange(add_skills=frozenset({(10, 1)})),
        platform_default_codes=frozenset(),
    )
    repo.install_skill(
        bot_id="bot",
        owner_id="owner",
        skill_id="1",
        engine_type="openclaw",
        default_engine_types=("openclaw",),
    )
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_DIRECT_ACTIVE"
    ):
        repo.replace_apply_skill_sets(
            mcp_catalog_entries={},
            scope=SCOPE,
            sets=(),
            remove_names=(),
            default_change=DefaultExclusionChange(remove_skills=frozenset({(10, 1)})),
            platform_default_codes=frozenset(),
        )
    assert repo.read_apply_snapshot(
        scope=SCOPE, platform_default_codes=frozenset()
    ).skill_exclusions == frozenset({(10, 1)})


def test_planned_default_exclusion_does_not_expand_to_later_member():
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(id=1, name="broken", git_path="center://broken", env="dev"),
                Skill(id=2, name="later", git_path="git://later", env="dev"),
                SkillSet(
                    id=10,
                    name="default",
                    is_default=True,
                    user_id="",
                    bolt_id="",
                    engine_type="openclaw",
                    env="dev",
                ),
            ]
        )
        session.add_all(
            [
                SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"),
                SkillSetSkill(skill_set_id=10, skill_id=2, env="dev"),
            ]
        )
    repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(),
        remove_names=(),
        default_change=DefaultExclusionChange(add_skills=frozenset({(10, 1)})),
        platform_default_codes=frozenset(),
    )
    after = repo.read_apply_snapshot(scope=SCOPE, platform_default_codes=frozenset())
    assert after.installed_skill_ids == frozenset({2})
    assert after.skill_exclusions == frozenset({(10, 1)})


def test_removing_stale_default_exclusion_does_not_restore_other_excluded_members():
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
    from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(id=1, name="broken", git_path="center://broken", env="dev"),
                SkillSet(
                    id=10,
                    name="default",
                    is_default=True,
                    user_id="",
                    bolt_id="",
                    engine_type="openclaw",
                    env="dev",
                ),
            ]
        )
        session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
        session.add_all(
            [
                DefaultSkillsetSkillExclusion(
                    user_id="owner", bot_id="bot", skill_set_id=10, skill_id=1
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner", bot_id="bot", skill_set_id=10, skill_id=99
                ),
            ]
        )
    repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(),
        remove_names=(),
        default_change=DefaultExclusionChange(remove_skills=frozenset({(10, 99)})),
        platform_default_codes=frozenset(),
    )
    after = repo.read_apply_snapshot(scope=SCOPE, platform_default_codes=frozenset())
    assert after.skill_exclusions == frozenset({(10, 1)})
    assert after.installed_skill_ids == frozenset()


def test_default_confirmation_distinguishes_noop_exclusion_from_unrelated_set_creation():
    from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill

    db = _Database()
    repo = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        session.add(Skill(id=1, name="one", git_path="git://one", env="dev"))
        session.add(
            SkillSet(
                id=10,
                name="default",
                is_default=True,
                user_id="",
                bolt_id="",
                engine_type="openclaw",
                env="dev",
            )
        )
        session.add(SkillSetSkill(skill_set_id=10, skill_id=1, env="dev"))
    change = DefaultExclusionChange(add_skills=frozenset({(10, 1)}))
    first = repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(),
        remove_names=(),
        default_change=change,
        platform_default_codes=frozenset(),
    )
    assert first.details["default_removed"] is True
    repeated = repo.replace_apply_skill_sets(
        mcp_catalog_entries={},
        scope=SCOPE,
        sets=(ResolvedSkillSetWrite("new"),),
        remove_names=(),
        default_change=change,
        platform_default_codes=frozenset(),
    )
    assert repeated.changed is True
    assert repeated.details["default_removed"] is False
    assert repeated.details["default_changed"] is False
