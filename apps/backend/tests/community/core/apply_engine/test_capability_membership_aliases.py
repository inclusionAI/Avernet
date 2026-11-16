"""Historical membership IDs never replace real Direct asset identities."""

from datetime import datetime

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillByCode, CenterSkillRef, Declaration, DesiredCapabilities, DesiredSkill,
)
from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion
from agentclaw.community.core.skill_center.skill_set_apply import DefaultExclusionChange
from agentclaw.community.core.models.skill_installation_source import SkillInstallationSourceKind
from agentclaw.community.core.repository.implementations.skill_center.tables import skill_installations
from ._capabilities_world import CapabilityWorld
from .test_unified_capabilities import apply


async def _historical_world(kind):
    world = CapabilityWorld()
    direct = world.shared("direct", "direct")
    # The supported historical shape: a Center member's raw ID points at
    # another Center asset while its UUID resolves to the current member.
    with world.db.transactional_orm_session() as session:
        asset = session.get(Skill, int(direct["id"]))
        asset.git_path = "center://direct"
        asset.skill_uuid = "11111111-1111-4111-8111-111111111111"
        asset.version = 1
        session.add(SkillVersion(
            skill_id=asset.id, version_ordinal=1, status="PUBLISHED",
            sc_version_number="1.0", sc_skill_id=17, sc_version_id=18,
            name="direct", metadata_json='{"mcp_dependencies":[]}',
            published_at=datetime(2026, 9, 1), created_by="owner", env="dev",
        ))
    initial = DesiredCapabilities(
        skills=Declaration((DesiredSkill("weather", CenterSkillByCode("weather")),))
    )
    assert (await apply(world.ctx, world.materialiser, initial)).status.value == "SUCCEEDED"
    center = world.skills.get_by_git_path("center://weather")
    who = dict(bot_id="bot-1", owner_id="entity-1", actor_id="owner", apply_id="seed")
    await world.activation.remove_apply_skill(**who, skill_id=center["id"])
    await world.activation.claim_apply_skill(**who, skill_id=direct["id"])
    with world.db.transactional_orm_session() as session:
        group = SkillSet(
            name="default" if kind == "default" else "retained",
            is_default=kind == "default", is_active=kind != "inactive",
            user_id="" if kind == "default" else "entity-1",
            bolt_id="" if kind == "default" else "bot-1", engine_type="moltis", env="dev",
        )
        session.add(group)
        session.flush()
        session.add(SkillSetSkill(
            skill_set_id=group.id, skill_id=int(direct["id"]),
            skill_uuid=center["skill_uuid"], env="dev",
        ))
    await world.sets.replace_apply_skill_sets(
        scope=world.snapshot().scope, actor_id="owner", sets=(), remove_names=(),
        default_change=DefaultExclusionChange(), platform_default_codes=frozenset(),
    )
    # Preserve the historical Direct source through the canonical table writer;
    # current relationship reconciliation is not the migration that created it.
    with world.db.transactional_orm_session() as session:
        skill_installations.install(
            session, bot_id="bot-1", owner_id="entity-1", env="dev",
            skill_id=int(direct["id"]), source_kind=SkillInstallationSourceKind.DIRECT,
        )
    return world, direct, center


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("kind", ["default", "active", "inactive"])
async def test_retained_set_alias_cannot_hide_an_unrelated_direct_installation(dry_run, kind):
    world, direct, center = await _historical_world(kind)
    before = world.snapshot()
    supplied = frozenset() if kind == "inactive" else frozenset({int(center["id"])})
    assert before.installed_skill_ids == supplied | {int(direct["id"])}
    result = await apply(
        world.ctx, world.materialiser,
        DesiredCapabilities(skills=Declaration(())), dry_run=dry_run,
    )
    assert result.status.value == "SUCCEEDED", result
    assert result.categories[0].removals == ("skills/direct",)
    after = world.snapshot()
    assert after.sets == before.sets
    assert after.skill_exclusions == before.skill_exclusions
    assert after.installed_skill_ids == (
        before.installed_skill_ids if dry_run else supplied
    )


@pytest.mark.asyncio
async def test_retry_of_direct_center_claim_respects_raw_default_exclusion():
    world, direct, center = await _historical_world("default")
    snapshot = world.snapshot()
    group = snapshot.sets[0]
    with world.db.transactional_orm_session() as session:
        session.add(DefaultSkillsetSkillExclusion(
            user_id="entity-1", bot_id="bot-1", skill_set_id=group.id,
            skill_id=int(direct["id"]), avernet_tenant=snapshot.scope.tenant,
        ))
        skill_installations.install(
            session, bot_id="bot-1", owner_id="entity-1", env="dev",
            skill_id=int(center["id"]), source_kind=SkillInstallationSourceKind.DIRECT,
        )
    before = world.snapshot()
    target = DesiredCapabilities(skills=Declaration((
        DesiredSkill("weather", CenterSkillRef(center["skill_uuid"])),
    )))
    result = await apply(world.ctx, world.materialiser, target)
    assert result.status.value == "SUCCEEDED", result
    assert [(entry.identity, entry.outcome.value) for entry in result.categories[0].entries] == [
        ("skills/weather", "unchanged"),
    ]
    after = world.snapshot()
    assert after.skill_exclusions == before.skill_exclusions
    assert after.installed_skill_ids == frozenset({int(center["id"])})
