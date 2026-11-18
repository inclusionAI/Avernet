"""Apply the anonymized 57/44 historical Default shape through SQLite."""

import pytest
from datetime import datetime

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DefaultSkillSetRef,
    DesiredCapabilities,
)
from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.models.skill_installation_source import (
    SkillInstallationSourceKind,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    skill_installations,
)
from agentclaw.community.core.skill_center.errors import (
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.skill_set_apply import DefaultExclusionChange
from ._capabilities_world import CapabilityWorld
from .test_unified_capabilities import apply


@pytest.mark.asyncio
async def test_default_dry_run_write_withdraw_restore_and_retry():
    world = CapabilityWorld()
    ids = [int(world.shared(f"sample-{i}", f"sample-{i}")["id"]) for i in range(13)]
    with world.db.transactional_orm_session() as session:
        group = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        session.add(group)
        session.flush()
        group_id = int(group.id)
        for i, skill_id in enumerate(ids):
            uuid = f"00000000-0000-4000-8000-{i + 1:012d}" if i < 4 else None
            session.get(Skill, skill_id).skill_uuid = uuid
            session.add(
                SkillSetSkill(
                    skill_set_id=group_id, skill_id=skill_id, skill_uuid=uuid, env="dev"
                )
            )
        for skill_id in range(10000, 10044):
            session.add(
                SkillSetSkill(skill_set_id=group_id, skill_id=skill_id, env="dev")
            )
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    before = world.snapshot()
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert not preview.categories[0].aborted
    assert world.snapshot() == before
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert world.snapshot().installed_skill_ids == frozenset(ids)
    assert {
        int(asset.skill_id)
        for asset in world.reader.active_skill_assets(
            bot_id="bot-1", owner_id="entity-1", bot=world.bot
        )
    } == set(ids)
    withdrawal = await apply(
        world.ctx, world.materialiser, DesiredCapabilities(skill_sets=Declaration(()))
    )
    assert not withdrawal.categories[0].aborted
    assert len(world.snapshot().skill_exclusions) == 57
    assert not world.snapshot().installed_skill_ids
    for _ in range(2):
        result = await apply(world.ctx, world.materialiser, target)
        assert not result.categories[0].aborted
        assert world.snapshot().installed_skill_ids == frozenset(ids)
        assert not world.snapshot().skill_exclusions
    with world.db.orm_session() as session:
        assert (
            session.query(SkillSetSkill).filter_by(skill_set_id=group_id).count() == 57
        )


@pytest.mark.asyncio
async def test_default_dependency_failure_aborts_preview_and_write():
    world = CapabilityWorld()
    skill_id = int(world.shared("broken", "broken")["id"])
    with world.db.transactional_orm_session() as session:
        session.get(Skill, skill_id).mcp_dependencies = "{"
        group = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        session.add(group)
        session.flush()
        session.add(SkillSetSkill(skill_set_id=group.id, skill_id=skill_id, env="dev"))
    before = world.snapshot()
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    for dry_run in (True, False):
        result = await apply(world.ctx, world.materialiser, target, dry_run=dry_run)
        assert result.categories[0].aborted
        assert world.snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["offline", "missing", "no_version"])
async def test_default_unavailable_recorded_installation_is_not_misclassified_as_direct(
    unavailable,
):
    world = CapabilityWorld()
    skill_id = int(world.shared("old", "old")["id"])
    with world.db.transactional_orm_session() as session:
        group = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        session.add(group)
        session.flush()
        session.add(SkillSetSkill(skill_set_id=group.id, skill_id=skill_id, env="dev"))
        skill_installations.install(
            session,
            bot_id="bot-1",
            owner_id="entity-1",
            env="dev",
            skill_id=skill_id,
            source_kind=SkillInstallationSourceKind.DEFAULT,
            source_set_id=int(group.id),
        )
        asset = session.get(Skill, skill_id)
        if unavailable == "offline":
            asset.offline_at = datetime(2026, 1, 1)
        elif unavailable == "missing":
            session.delete(asset)
        else:
            asset.git_path = "center://old"
            asset.skill_uuid = "00000000-0000-4000-8000-000000000001"
            session.query(SkillSetSkill).filter_by(
                skill_set_id=group.id
            ).one().skill_uuid = asset.skill_uuid
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    before = world.snapshot()
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert not preview.categories[0].aborted
    assert world.snapshot() == before
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert not world.snapshot().installed_skill_ids


@pytest.mark.asyncio
async def test_default_does_not_take_over_existing_direct_installation():
    world = CapabilityWorld()
    skill_id = int(world.shared("owned", "owned")["id"])
    with world.db.transactional_orm_session() as session:
        group = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        session.add(group)
        session.flush()
        session.add(SkillSetSkill(skill_set_id=group.id, skill_id=skill_id, env="dev"))
        skill_installations.install(
            session,
            bot_id="bot-1",
            owner_id="entity-1",
            env="dev",
            skill_id=skill_id,
            source_kind=SkillInstallationSourceKind.DIRECT,
        )
    before = world.snapshot()
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    for dry_run in (True, False):
        result = await apply(world.ctx, world.materialiser, target, dry_run=dry_run)
        assert result.categories[0].aborted
        assert world.snapshot() == before


def test_reader_rejects_invalid_default_dependencies_before_installation_write():
    world = CapabilityWorld()
    skill_id = int(world.shared("center", "center")["id"])
    with world.db.transactional_orm_session() as session:
        skill = session.get(Skill, skill_id)
        skill.git_path = "center://center"
        skill.skill_uuid = "00000000-0000-4000-8000-000000000001"
        group = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        session.add(group)
        session.flush()
        session.add(
            SkillSetSkill(
                skill_set_id=group.id,
                skill_id=skill_id,
                skill_uuid=skill.skill_uuid,
                env="dev",
            )
        )
        session.add(
            SkillVersion(
                skill_id=skill_id,
                version_ordinal=1,
                status="PUBLISHED",
                sc_version_number="1",
                name="center",
                metadata_json="{",
                created_by="owner",
                env="dev",
            )
        )
    with pytest.raises(ValueError):
        world.reader.active_skill_assets(
            bot_id="bot-1", owner_id="entity-1", bot=world.bot
        )
    assert not world.snapshot().installed_skill_ids


@pytest.mark.parametrize("same_asset", [True, False])
def test_reader_rejects_direct_conflict_when_default_becomes_available(same_asset):
    world = CapabilityWorld()
    direct_id = int(world.shared("collision", "direct")["id"])
    default_id = (
        direct_id if same_asset else int(world.shared("collision", "default")["id"])
    )
    with world.db.transactional_orm_session() as session:
        group = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        session.add(group)
        session.flush()
        session.add(
            SkillSetSkill(skill_set_id=group.id, skill_id=default_id, env="dev")
        )
        skill_installations.install(
            session,
            bot_id="bot-1",
            owner_id="entity-1",
            env="dev",
            skill_id=direct_id,
            source_kind=SkillInstallationSourceKind.DIRECT,
        )
    before = world.snapshot()
    with pytest.raises(
        SkillSetControlPlaneConflictError
        if same_asset
        else SkillRuntimeNameConflictError
    ):
        world.reader.active_skill_assets(
            bot_id="bot-1", owner_id="entity-1", bot=world.bot
        )
    assert world.snapshot() == before


@pytest.mark.parametrize("with_uuid", [False, True])
def test_reader_rejects_inactive_ordinary_set_ownership(with_uuid):
    world = CapabilityWorld()
    skill_id = int(world.shared("shared", "shared")["id"])
    with world.db.transactional_orm_session() as session:
        uuid = "00000000-0000-4000-8000-000000000001" if with_uuid else None
        session.get(Skill, skill_id).skill_uuid = uuid
        default = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="other",
            user_id="entity-1",
            bolt_id="bot-1",
            engine_type="moltis",
            is_default=False,
            is_active=False,
            env="dev",
        )
        session.add_all([default, ordinary])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=row.id, skill_id=skill_id, skill_uuid=uuid, env="dev"
                )
                for row in (default, ordinary)
            ]
        )
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
    ):
        world.reader.active_skill_assets(
            bot_id="bot-1", owner_id="entity-1", bot=world.bot
        )
    assert not world.snapshot().installed_skill_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("with_uuid", [False, True])
async def test_apply_cannot_transfer_ordinary_member_by_deleting_its_set(with_uuid):
    world = CapabilityWorld()
    skill_id = int(world.shared("shared", "shared")["id"])
    with world.db.transactional_orm_session() as session:
        uuid = "00000000-0000-4000-8000-000000000001" if with_uuid else None
        session.get(Skill, skill_id).skill_uuid = uuid
        default = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="other",
            user_id="entity-1",
            bolt_id="bot-1",
            engine_type="moltis",
            is_default=False,
            is_active=False,
            env="dev",
        )
        session.add_all([default, ordinary])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=row.id, skill_id=skill_id, skill_uuid=uuid, env="dev"
                )
                for row in (default, ordinary)
            ]
        )
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    before = world.snapshot()
    for dry_run in (True, False):
        result = await apply(world.ctx, world.materialiser, target, dry_run=dry_run)
        assert result.categories[0].aborted
        assert world.snapshot() == before
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
    ):
        world.repo.replace_apply_skill_sets(
            scope=before.scope,
            sets=(),
            remove_names=("other",),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
            mcp_catalog_entries={},
        )
    assert world.snapshot() == before


@pytest.mark.asyncio
async def test_stale_repo_uuid_does_not_claim_ordinary_set_ownership():
    world = CapabilityWorld()
    skill_id = int(world.shared("shared", "shared")["id"])
    uuid = "00000000-0000-4000-8000-000000000001"
    with world.db.transactional_orm_session() as session:
        session.get(Skill, skill_id).skill_uuid = uuid
        default = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="other",
            user_id="entity-1",
            bolt_id="bot-1",
            engine_type="moltis",
            is_default=False,
            is_active=False,
            env="dev",
        )
        session.add_all([default, ordinary])
        session.flush()
        session.add(
            SkillSetSkill(
                skill_set_id=default.id, skill_id=skill_id, skill_uuid=uuid, env="dev"
            )
        )
        session.add(
            SkillSetSkill(
                skill_set_id=ordinary.id, skill_id=999999, skill_uuid=uuid, env="dev"
            )
        )
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert not preview.categories[0].aborted
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert world.snapshot().installed_skill_ids == frozenset({skill_id})
    assert [row.name for row in world.snapshot().sets] == ["default"]


@pytest.mark.asyncio
async def test_repo_ordinary_ownership_uses_raw_id_when_relation_uuid_differs():
    world = CapabilityWorld()
    skill_id = int(world.shared("shared", "shared")["id"])
    with world.db.transactional_orm_session() as session:
        session.get(Skill, skill_id).skill_uuid = "00000000-0000-4000-8000-000000000001"
        default = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="other",
            user_id="entity-1",
            bolt_id="bot-1",
            engine_type="moltis",
            is_default=False,
            is_active=False,
            env="dev",
        )
        session.add_all([default, ordinary])
        session.flush()
        session.add(
            SkillSetSkill(skill_set_id=default.id, skill_id=skill_id, env="dev")
        )
        session.add(
            SkillSetSkill(
                skill_set_id=ordinary.id,
                skill_id=skill_id,
                skill_uuid="00000000-0000-4000-8000-000000000002",
                env="dev",
            )
        )
    before = world.snapshot()
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    for dry_run in (True, False):
        result = await apply(world.ctx, world.materialiser, target, dry_run=dry_run)
        assert result.categories[0].aborted
        assert world.snapshot() == before
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
    ):
        world.reader.active_skill_assets(
            bot_id="bot-1", owner_id="entity-1", bot=world.bot
        )
    assert world.snapshot() == before
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
    ):
        world.repo.replace_apply_skill_sets(
            scope=before.scope,
            sets=(),
            remove_names=("other",),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
            mcp_catalog_entries={},
        )
    assert world.snapshot() == before


@pytest.mark.parametrize("published", [True, False])
def test_ordinary_raw_repo_and_uuid_center_follow_existing_supply(published):
    from agentclaw.community.core.apply_engine.materialisers.capabilities_resolve import (
        resolve_supply,
    )
    from agentclaw.community.core.apply_engine.materialisers.capabilities_types import (
        effective_sets,
    )

    world = CapabilityWorld()
    repo_id = int(world.shared("repo", "repo")["id"])
    uuid = "00000000-0000-4000-8000-000000000001"
    with world.db.transactional_orm_session() as session:
        center = Skill(
            name="center",
            git_path="center://center",
            skill_uuid=uuid,
            status="PUBLISHED" if published else "DEVELOPING",
            version=1,
            env="dev",
        )
        group = SkillSet(
            name="other",
            user_id="entity-1",
            bolt_id="bot-1",
            engine_type="moltis",
            is_default=False,
            is_active=True,
            env="dev",
        )
        session.add_all([center, group])
        session.flush()
        center_id = int(center.id)
        session.add(
            SkillVersion(
                skill_id=center_id,
                version_ordinal=1,
                status="PUBLISHED",
                sc_version_number="1",
                name="center",
                metadata_json='{"mcp_dependencies":[]}',
                created_by="owner",
                env="dev",
            )
        )
        session.add(
            SkillSetSkill(
                skill_set_id=group.id, skill_id=repo_id, skill_uuid=uuid, env="dev"
            )
        )
    facts = world.snapshot()
    expected = {repo_id, center_id} if published else {repo_id}
    assert effective_sets(facts)[0] == expected
    resolved, failures = resolve_supply(DesiredCapabilities(), facts, (), (), ())
    assert not failures
    assert {int(skill.skill_id) for skill in resolved.final_skills} == expected
    assert (
        world.repo.list_member_skill_ids(
            bot_id="bot-1",
            owner_id="entity-1",
            engine_type="moltis",
            default_engine_types=("moltis",),
        )
        == expected
    )
