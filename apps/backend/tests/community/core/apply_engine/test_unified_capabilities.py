"""Capability replacement through the existing Apply and authorized SQLite service seams."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.capabilities import (
    Category,
    resolve_capabilities,
)
from agentclaw.community.core.apply_engine.desired_state import (
    CapabilityDeclaration,
    Declaration,
    DesiredCapabilities,
    DesiredState,
)
from agentclaw.community.core.apply_engine.delivery import ArcaDelivery
from agentclaw.community.core.apply_engine.orchestrator import ApplyOrchestrator
from agentclaw.community.core.apply_engine.materialisers.capabilities import (
    CapabilitiesMaterialiser,
)
from agentclaw.community.core.apply_engine.skill_set_apply_delegates import (
    PlatformSkillSetApply,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from .test_capability_full_replace_contract import _Database
from tests.community.core.skill_center._test_skill_set_management_service_support import (
    _skill_service,
    _Runtime,
)
from ._fakes import FakeManifestContent, FakeSharedSkills, FakeMcpConfigService, real_validator


def rig():
    service = _skill_service(
        CapabilityDesiredStateRepository(_Database(shared=True)), _Runtime()
    )
    sets = PlatformSkillSetApply(service)
    ctx = ApplyContext(
        bot_id="bot-1",
        owner_id="true-owner",
        entity_id="true-owner",
        actor_id="true-owner",
        env="dev",
        tenant="default",
        engine_type="openclaw",
        bot_type="personal",
        bot={},
        capabilities=resolve_capabilities(
            active_engine="openclaw", bot_type="personal", is_teclaw=lambda _: False
        ),
    )
    materialiser = CapabilitiesMaterialiser(
        SimpleNamespace(),
        SimpleNamespace(),
        real_validator(),
        FakeManifestContent(),
        FakeSharedSkills(),
        SimpleNamespace(),
        sets,
        SimpleNamespace(
            check_mcp_permission_detail=lambda *_: {
                "has_permission": True,
                "access_level": "PUBLIC",
            }
        ),
        FakeMcpConfigService(),
    )
    return service, sets, ctx, materialiser


async def apply(ctx, materialiser, target, *, dry_run=False):
    state = DesiredState(capabilities=CapabilityDeclaration.from_target(target))
    return await ApplyOrchestrator(
        {Category.CAPABILITIES: materialiser},
        steps=ArcaDelivery(lambda: None).steps_for,
    ).apply(
        ctx,
        state,
        apply_id="test",
        trigger="explicit",
        started_at=datetime.now(),
        dry_run=dry_run,
    )


@pytest.mark.asyncio
async def test_all_empty_capability_target_deletes_ordinary_set_and_converges():
    service, sets, ctx, materialiser = rig()
    from agentclaw.community.core.models.skill import SkillSet

    with service._repository._db.transactional_orm_session() as session:
        session.add(
            SkillSet(
                name="default",
                user_id="",
                bolt_id="",
                engine_type="openclaw",
                is_default=True,
                is_active=True,
                env="dev",
            )
        )
    before = sets.read_apply_snapshot(
        bot_id=ctx.bot_id, owner_id=ctx.entity_id, actor_id=ctx.actor_id
    )
    await sets.replace_apply_skill_sets(
        scope=before.scope,
        actor_id=ctx.actor_id,
        sets=(ResolvedSkillSetWrite("old"),),
        remove_names=(),
        default_change=DefaultExclusionChange(),
        platform_default_codes=before.platform_default_codes,
    )
    target = DesiredCapabilities(
        skills=Declaration(()), skill_sets=Declaration(()), mcp=Declaration(())
    )
    preview = await apply(ctx, materialiser, target, dry_run=True)
    assert preview.categories[0].removals == ("skill_sets/default", "skill_sets/old")
    assert (
        len(
            sets.read_apply_snapshot(
                bot_id=ctx.bot_id, owner_id=ctx.entity_id, actor_id=ctx.actor_id
            ).sets
        )
        == 2
    )
    result = await apply(ctx, materialiser, target)
    assert result.categories[0].removals == ("skill_sets/default", "skill_sets/old")
    assert (
        sets.read_apply_snapshot(
            bot_id=ctx.bot_id, owner_id=ctx.entity_id, actor_id=ctx.actor_id
        )
        .sets[0]
        .name
        == "default"
    )
    again = await apply(ctx, materialiser, target)
    assert again.categories[0].removals == ()


@pytest.mark.asyncio
async def test_empty_set_target_refuses_policy_exclusion_without_actual_default_scope():
    _service, _sets, ctx, materialiser = rig()
    report = await apply(
        ctx, materialiser, DesiredCapabilities(skill_sets=Declaration(()))
    )
    category = report.categories[0]
    assert category.aborted
    assert "Default" in category.entries[0].reason


@pytest.mark.asyncio
async def test_declared_ordinary_set_creates_real_membership_scope_and_reuses_id():
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkillSet,
        DefaultSkillSetRef,
    )

    _service, sets, ctx, materialiser = rig()
    target = DesiredCapabilities(
        skill_sets=Declaration(
            (DefaultSkillSetRef(), DesiredSkillSet("expert", False, (), (), "notes"))
        )
    )
    first = await apply(ctx, materialiser, target)
    assert first.categories[0].entries[-1].outcome.value == "created"
    snapshot = sets.read_apply_snapshot(
        bot_id=ctx.bot_id, owner_id=ctx.entity_id, actor_id=ctx.actor_id
    )
    assert [(s.name, s.description, s.is_active) for s in snapshot.sets] == [
        ("expert", "notes", False)
    ]
    again = await apply(ctx, materialiser, target)
    assert again.categories[0].entries[-1].outcome.value == "unchanged"
    assert (
        sets.read_apply_snapshot(
            bot_id=ctx.bot_id, owner_id=ctx.entity_id, actor_id=ctx.actor_id
        )
        .sets[0]
        .id
        == snapshot.sets[0].id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_set", [False, True])
async def test_active_set_cannot_take_different_asset_current_direct_name_even_when_direct_is_removed(
    existing_set,
):
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        DesiredSkillSet,
        RepoSkillPath,
    )

    world = CapabilityWorld()
    direct = world.shared("review", "direct")
    member = world.shared("review", "member")
    if existing_set:
        await world.sets.replace_apply_skill_sets(
            scope=world.snapshot().scope,
            actor_id="owner",
            sets=(
                ResolvedSkillSetWrite(
                    "expert", is_active=False, skill_ids=(int(member["id"]),)
                ),
            ),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
        )
    await world.activation.claim_apply_skill(
        skill_id=direct["id"],
        bot_id="bot-1",
        owner_id="entity-1",
        actor_id="owner",
        apply_id="seed",
    )
    target = DesiredCapabilities(
        skills=Declaration(()),
        skill_sets=Declaration(
            (
                DesiredSkillSet(
                    "expert",
                    True,
                    (DesiredSkill("review", RepoSkillPath("member")),),
                    (),
                ),
            )
        ),
    )
    for dry_run in (True, False):
        report = await apply(world.ctx, world.materialiser, target, dry_run=dry_run)
        assert report.categories[0].aborted
        assert "RESOURCE_DIRECT_ACTIVE" in report.categories[0].entries[0].reason
        snapshot = world.snapshot()
        assert snapshot.installed_skill_ids == frozenset({int(direct["id"])})
        assert all(not group.is_active for group in snapshot.sets)


@pytest.mark.asyncio
async def test_repo_direct_retains_actual_dependency_and_clears_omitted_override():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        RepoSkillPath,
    )
    from agentclaw.community.core.models.mcp import BotMCPConfig

    world = CapabilityWorld()
    shared = world.shared("review", "review", dependencies=({"code": "mcp.dep"},))
    with world.db.transactional_orm_session() as session:
        session.add(
            BotMCPConfig(
                bot_id="bot-1",
                owner_id="entity-1",
                server_code="mcp.dep",
                config='{"headers":{"Old":"value"}}',
                env="dev",
            )
        )
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("review", RepoSkillPath("review")),)),
        mcp=Declaration(()),
    )
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert not preview.categories[0].aborted
    assert {e.identity: e.outcome.value for e in preview.categories[0].entries} == {
        "skills/review": "created",
        "mcp/mcp.dep": "updated",
    }
    assert world.snapshot().installed_skill_ids == frozenset()
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    snapshot = world.snapshot()
    assert snapshot.installed_skill_ids == frozenset({int(shared["id"])})
    assert snapshot.mcp_overrides == {}
    again = await apply(world.ctx, world.materialiser, target)
    assert [(e.identity, e.outcome.value) for e in again.categories[0].entries] == [
        ("skills/review", "unchanged")
    ]


@pytest.mark.asyncio
async def test_first_public_preview_is_read_only_and_actual_apply_prepares_exact_shared_asset():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        CenterSkillByCode,
    )

    world = CapabilityWorld()
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("weather", CenterSkillByCode("weather")),))
    )
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert not preview.categories[0].aborted
    assert world.skills.get_by_git_path("center://weather") is None
    assert world.snapshot().installed_skill_ids == frozenset()
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    shared = world.skills.get_by_git_path("center://weather")
    assert world.snapshot().installed_skill_ids == frozenset({int(shared["id"])})
    again = await apply(world.ctx, world.materialiser, target)
    assert [(e.identity, e.outcome.value) for e in again.categories[0].entries] == [
        ("skills/weather", "unchanged")
    ]


def local_skill(world, name):
    import hashlib
    from agentclaw.community.core.content_store import canonical_tree_bytes
    from agentclaw.community.core.apply_engine.desired_state import (
        ContentRef,
        DesiredSkill,
        LocalSkillPackage,
    )

    tree = canonical_tree_bytes(
        [
            (
                "SKILL.md",
                f"---\nname: {name}\ndescription: Skill package\n---\nBody".encode(),
            )
        ]
    )
    digest = "sha256:" + hashlib.sha256(tree).hexdigest()
    world.content.blobs[digest] = tree
    return DesiredSkill(name, LocalSkillPackage(ContentRef(digest)))


@pytest.mark.asyncio
async def test_set_local_upload_never_claims_direct_and_inactive_reference_survives_empty_skills():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import DesiredSkillSet

    world = CapabilityWorld()
    target = DesiredCapabilities(
        skills=Declaration(()),
        skill_sets=Declaration(
            (DesiredSkillSet("expert", False, (local_skill(world, "local-one"),), ()),)
        ),
    )
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    first = world.snapshot()
    assert first.sets[0].name == "expert"
    assert len(first.sets[0].skill_ids) == 1
    assert first.installed_skill_ids == frozenset()
    assert world.uploads.packages.keys() == {"local-one"}
    # Genome's missing collection target does not erase the inactive set or its Local package.
    result = await apply(
        world.ctx, world.materialiser, DesiredCapabilities(skills=Declaration(()))
    )
    assert not result.categories[0].aborted
    assert world.snapshot().sets == first.sets
    assert world.uploads.packages.keys() == {"local-one"}


@pytest.mark.asyncio
async def test_local_cleanup_runs_after_success_and_protects_excluded_default_raw_member():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.models.skill import SkillSet, SkillSetSkill
    from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

    world = CapabilityWorld()
    package = local_skill(world, "saved")
    assert (
        not (
            await apply(
                world.ctx,
                world.materialiser,
                DesiredCapabilities(skills=Declaration((package,))),
            )
        )
        .categories[0]
        .aborted
    )
    local = world.snapshot().skills[0]
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
            SkillSetSkill(skill_set_id=group.id, skill_id=int(local["id"]), env="dev")
        )
        session.add(
            DefaultSkillsetSkillExclusion(
                bot_id="bot-1",
                user_id="entity-1",
                skill_set_id=group.id,
                skill_id=int(local["id"]),
            )
        )
    report = await apply(
        world.ctx, world.materialiser, DesiredCapabilities(skills=Declaration(()))
    )
    assert not report.categories[0].aborted
    assert world.snapshot().installed_skill_ids == frozenset()
    assert world.uploads.packages.keys() == {"saved"}


@pytest.mark.asyncio
async def test_direct_local_omission_physically_deletes_unreferenced_package():
    from ._capabilities_world import CapabilityWorld

    world = CapabilityWorld()
    assert (
        not (
            await apply(
                world.ctx,
                world.materialiser,
                DesiredCapabilities(
                    skills=Declaration((local_skill(world, "obsolete"),))
                ),
            )
        )
        .categories[0]
        .aborted
    )
    result = await apply(
        world.ctx, world.materialiser, DesiredCapabilities(skills=Declaration(()))
    )
    assert not result.categories[0].aborted
    assert result.categories[0].removals == ("skills/obsolete",)
    assert world.uploads.packages == {}
    assert world.snapshot().skills == ()


@pytest.mark.asyncio
async def test_unchanged_active_set_repairs_missing_installation_and_dry_run_marks_write():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        DesiredSkillSet,
        RepoSkillPath,
    )
    from agentclaw.community.core.models.skill import BotSkillInstallation

    world = CapabilityWorld()
    shared = world.shared("review", "review")
    target = DesiredCapabilities(
        skill_sets=Declaration(
            (
                DesiredSkillSet(
                    "expert",
                    True,
                    (DesiredSkill("review", RepoSkillPath("review")),),
                    (),
                ),
            )
        )
    )
    assert (
        not (await apply(world.ctx, world.materialiser, target)).categories[0].aborted
    )
    with world.db.transactional_orm_session() as session:
        session.query(BotSkillInstallation).delete()
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert world.snapshot().installed_skill_ids == frozenset({int(shared["id"])})


@pytest.mark.asyncio
async def test_default_withdrawal_then_direct_takeover_and_restoration_refusal():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        DesiredMcpServer,
        DefaultSkillSetRef,
        RepoSkillPath,
    )
    from agentclaw.community.core.models.skill import SkillSet, SkillSetSkill
    from agentclaw.community.core.models.mcp import SkillSetMCPServer

    world = CapabilityWorld()
    shared = world.shared("review", "review")
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
            SkillSetSkill(skill_set_id=group.id, skill_id=int(shared["id"]), env="dev")
        )
        session.add(
            SkillSetMCPServer(
                skill_set_id=group.id,
                server_code="mcp.default",
                name="Default MCP",
                env="dev",
            )
        )
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("review", RepoSkillPath("review")),)),
        skill_sets=Declaration(()),
        mcp=Declaration((DesiredMcpServer("mcp.default"),)),
    )
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert not preview.categories[0].aborted
    assert preview.categories[0].removals == ("skill_sets/default",)
    assert world.snapshot().skill_exclusions == frozenset()
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    installed = world.snapshot()
    assert installed.installed_skill_ids == frozenset({int(shared["id"])})
    assert installed.installed_mcp_codes == frozenset({"mcp.default"})
    assert len(installed.skill_exclusions) == len(installed.mcp_exclusions) == 1
    restore = await apply(
        world.ctx,
        world.materialiser,
        DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),))),
    )
    assert restore.categories[0].aborted
    assert "restoration conflicts" in restore.categories[0].entries[0].reason
    assert world.snapshot() == installed


@pytest.mark.asyncio
async def test_default_restore_resets_actual_exclusions_and_becomes_noop():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import DefaultSkillSetRef
    from agentclaw.community.core.models.skill import SkillSet, SkillSetSkill
    from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

    world = CapabilityWorld()
    shared = world.shared("review", "review")
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
            SkillSetSkill(skill_set_id=group.id, skill_id=int(shared["id"]), env="dev")
        )
        session.add(
            DefaultSkillsetSkillExclusion(
                bot_id="bot-1",
                user_id="entity-1",
                skill_set_id=group.id,
                skill_id=int(shared["id"]),
            )
        )
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    result = await apply(world.ctx, world.materialiser, target)
    assert [(e.identity, e.outcome.value) for e in result.categories[0].entries] == [
        ("skill_sets/default", "updated")
    ]
    assert world.snapshot().skill_exclusions == frozenset()
    assert world.snapshot().installed_skill_ids == frozenset({int(shared["id"])})
    again = await apply(world.ctx, world.materialiser, target)
    assert [(e.identity, e.outcome.value) for e in again.categories[0].entries] == [
        ("skill_sets/default", "unchanged")
    ]


@pytest.mark.asyncio
async def test_failed_shared_preparation_prevents_local_upload_and_all_bot_writes():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.testing.canonical_center_store import (
        LocalCanonicalCenterVersionStore,
    )
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        CenterSkillByCode,
    )

    class FailedStore(LocalCanonicalCenterVersionStore):
        def write_version(self, version):
            raise RuntimeError("canonical object store unavailable")

    world = CapabilityWorld(store=FailedStore())
    target = DesiredCapabilities(
        skills=Declaration(
            (
                local_skill(world, "local-one"),
                DesiredSkill("weather", CenterSkillByCode("weather")),
            )
        )
    )
    result = await apply(world.ctx, world.materialiser, target)
    assert result.status.value == "FAILED"
    assert (
        result.categories[0].partially_written
        and not result.categories[0].confirmed_partial
    )
    assert result.categories[0].removals == ()
    assert world.uploads.packages == {}
    assert world.snapshot().installed_skill_ids == frozenset()
    assert world.snapshot().sets == ()


@pytest.mark.asyncio
async def test_local_upload_then_failed_set_transaction_is_partial_and_stops_direct_mcp_and_cleanup():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkillSet,
        DesiredMcpServer,
    )
    from sqlalchemy import event

    world = CapabilityWorld()
    assert (
        not (
            await apply(
                world.ctx,
                world.materialiser,
                DesiredCapabilities(
                    skills=Declaration((local_skill(world, "old-local"),))
                ),
            )
        )
        .categories[0]
        .aborted
    )

    def fail_set(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO ac_skill_set "):
            raise RuntimeError("database write failed")

    event.listen(world.db.engine, "before_cursor_execute", fail_set)
    target = DesiredCapabilities(
        skills=Declaration(()),
        skill_sets=Declaration(
            (DesiredSkillSet("expert", True, (local_skill(world, "new-local"),), ()),)
        ),
        mcp=Declaration((DesiredMcpServer("mcp.new"),)),
    )
    result = await apply(world.ctx, world.materialiser, target)
    event.remove(world.db.engine, "before_cursor_execute", fail_set)
    assert result.status.value == "PARTIAL"
    assert result.categories[0].confirmed_partial
    assert result.categories[0].removals == ()
    snapshot = world.snapshot()
    assert snapshot.sets == ()
    assert len(snapshot.installed_skill_ids) == 1
    assert snapshot.installed_mcp_codes == frozenset()
    assert world.uploads.packages.keys() == {"old-local", "new-local"}


@pytest.mark.asyncio
async def test_failed_direct_claim_after_set_commit_preserves_set_and_stops_mcp():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        DesiredSkillSet,
        DesiredMcpServer,
        RepoSkillPath,
    )
    from sqlalchemy import event

    world = CapabilityWorld()
    world.shared("review", "review")

    def fail_claim(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if statement.startswith("INSERT INTO ac_bot_skill_installation "):
            raise RuntimeError("database write failed")

    event.listen(world.db.engine, "before_cursor_execute", fail_claim)
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("review", RepoSkillPath("review")),)),
        skill_sets=Declaration((DesiredSkillSet("expert", False, (), ()),)),
        mcp=Declaration((DesiredMcpServer("mcp.new"),)),
    )
    result = await apply(world.ctx, world.materialiser, target)
    event.remove(world.db.engine, "before_cursor_execute", fail_claim)
    assert result.status.value == "PARTIAL"
    assert result.categories[0].removals == ()
    snapshot = world.snapshot()
    assert [(s.name, s.is_active) for s in snapshot.sets] == [("expert", False)]
    assert snapshot.installed_skill_ids == frozenset()
    assert snapshot.installed_mcp_codes == frozenset()


@pytest.mark.asyncio
async def test_repo_identity_remains_catalog_id_when_legacy_row_also_has_uuid():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        DesiredSkillSet,
        RepoSkillPath,
    )

    world = CapabilityWorld()
    shared = world.shared("review", "review")
    from agentclaw.community.core.models.skill import Skill

    with world.db.transactional_orm_session() as session:
        session.get(
            Skill, int(shared["id"])
        ).skill_uuid = "9f7c5428-1a56-40fc-b542-dd226d2e7d48"
    target = DesiredCapabilities(
        skill_sets=Declaration(
            (
                DesiredSkillSet(
                    "expert",
                    True,
                    (DesiredSkill("review", RepoSkillPath("review")),),
                    (),
                ),
            )
        )
    )
    first = await apply(world.ctx, world.materialiser, target)
    assert not first.categories[0].aborted
    again = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert [(e.identity, e.outcome.value) for e in again.categories[0].entries] == [
        ("skill_sets/expert", "unchanged")
    ]


@pytest.mark.asyncio
async def test_uuid_and_ready_code_are_one_asset_for_inactive_set_ownership():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        DesiredSkillSet,
        CenterSkillByCode,
        CenterSkillRef,
    )

    world = CapabilityWorld()
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("weather", CenterSkillByCode("weather")),))
    )
    assert (
        not (await apply(world.ctx, world.materialiser, target)).categories[0].aborted
    )
    asset = world.skills.get_by_git_path("center://weather")
    await world.activation.remove_apply_skill(
        skill_id=asset["id"],
        bot_id="bot-1",
        owner_id="entity-1",
        actor_id="owner",
        apply_id="setup",
    )
    target = DesiredCapabilities(
        skill_sets=Declaration(
            (
                DesiredSkillSet(
                    "one",
                    False,
                    (DesiredSkill("weather", CenterSkillByCode("weather")),),
                    (),
                ),
                DesiredSkillSet(
                    "two",
                    False,
                    (DesiredSkill("weather", CenterSkillRef(asset["skill_uuid"])),),
                    (),
                ),
            )
        )
    )
    report = await apply(world.ctx, world.materialiser, target)
    assert report.categories[0].aborted
    assert any(
        "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET" in (e.reason or "")
        for e in report.categories[0].entries
    )
    assert world.snapshot().sets == ()


@pytest.mark.asyncio
async def test_public_preview_spends_shared_budget_and_never_publishes_when_exhausted(
    monkeypatch,
):
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine import fetch_budget
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        CenterSkillByCode,
    )

    world = CapabilityWorld()
    monkeypatch.setattr(fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", 5)
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("weather", CenterSkillByCode("weather")),))
    )
    with fetch_budget.fetch_budget_scope():
        result = await apply(world.ctx, world.materialiser, target, dry_run=True)
        assert result.categories[0].aborted
        assert "budget is exhausted (bytes)" in result.categories[0].entries[0].reason
    assert len(world.gateway.calls) == 1
    assert world.skills.get_by_git_path("center://weather") is None
    assert world.snapshot().installed_skill_ids == frozenset()


@pytest.mark.asyncio
async def test_expired_shared_budget_refuses_public_preview_before_download(
    monkeypatch,
):
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine import fetch_budget
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        CenterSkillByCode,
    )

    world = CapabilityWorld()
    monkeypatch.setattr(fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", 5)
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("weather", CenterSkillByCode("weather")),))
    )
    with fetch_budget.fetch_budget_scope():
        fetch_budget.current_fetch_budget().charge(5)
        result = await apply(world.ctx, world.materialiser, target, dry_run=True)
        assert result.categories[0].aborted
        assert "budget is exhausted (bytes)" in result.categories[0].entries[0].reason
    assert world.gateway.calls == []


@pytest.mark.asyncio
async def test_mcp_permission_failure_aborts_capability_group_and_other_category_still_writes():
    from ._capabilities_world import CapabilityWorld
    from agentclaw.community.core.apply_engine.constructs import Section
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredScript,
        DesiredMcpServer,
        DesiredSkillSet,
    )
    from agentclaw.community.core.apply_engine.materialisers.script import (
        ScriptMaterialiser,
    )
    from ._fakes import FakeStartupScriptService

    class Denied:
        def check_mcp_permission_detail(self, *_args):
            return {"has_permission": False, "access_level": "PUBLIC"}

    world = CapabilityWorld(auth=Denied())
    target = DesiredCapabilities(
        skills=Declaration((local_skill(world, "new-local"),)),
        skill_sets=Declaration((DesiredSkillSet("expert", False, (), ()),)),
        mcp=Declaration((DesiredMcpServer("mcp.denied"),)),
    )
    scripts = FakeStartupScriptService()
    state = DesiredState(
        capabilities=CapabilityDeclaration.from_target(target),
        script=Declaration((DesiredScript("echo applied"),)),
    )
    result = await ApplyOrchestrator(
        {
            Section.SCRIPT: ScriptMaterialiser(scripts),
            Category.CAPABILITIES: world.materialiser,
        },
        steps=ArcaDelivery(lambda: None).steps_for,
    ).apply(
        world.ctx, state, apply_id="test", trigger="explicit", started_at=datetime.now()
    )
    assert scripts.body == "echo applied"
    assert result.categories[-1].aborted
    assert world.snapshot().sets == ()
    assert world.uploads.packages == {}
