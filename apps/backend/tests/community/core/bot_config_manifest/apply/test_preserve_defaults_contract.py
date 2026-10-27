"""Default-preserving Replace exercised through real desired-state persistence."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from . import test_capability_full_replace_contract as base
from agentclaw.community.core.models.skill import SkillSet, SkillSetSkill
from agentclaw.community.core.models.mcp import SkillSetMCPServer, BotMCPConfig
from agentclaw.community.core.models.skill_installation_source import (
    BotSkillInstallationSource,
    SkillInstallationSourceKind,
)
from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetSkillExclusion,
    DefaultSkillsetMcpExclusion,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    skill_installations,
)


def setup_case():
    db = base._Database()
    bots = base.BotRepository(db)
    skills = base.SkillRepository(db)
    old_id = base._seed(db, bots, skills)
    default = skills.create(
        {
            "name": "default-tool",
            "description": "shared",
            "git_path": "repo/default-tool",
            "is_public": True,
            "user_id": "",
            "bolt_id": "",
            "status": "PUBLISHED",
            "mcp_dependencies": [{"code": "mcp.default-dep"}],
        }
    )
    default_id = int(default["id"])
    with db.transactional_orm_session() as session:
        ordinary = session.query(SkillSet).filter_by(name="ordinary").one()
        skill_installations.install(
            session,
            bot_id="bot-1",
            owner_id="entity-1",
            env="dev",
            skill_id=old_id,
            source_kind=SkillInstallationSourceKind.SKILL_SET,
            source_set_id=ordinary.id,
        )
        row = SkillSet(
            name="Default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            is_active=True,
            env="dev",
        )
        session.add(row)
        session.flush()
        set_id = int(row.id)
        session.add(SkillSetSkill(skill_set_id=set_id, skill_id=default_id, env="dev"))
        session.add(
            SkillSetMCPServer(
                skill_set_id=set_id,
                server_code="mcp.default",
                name="default",
                env="dev",
            )
        )
        session.add(
            BotMCPConfig(
                bot_id="bot-1",
                owner_id="entity-1",
                server_code="mcp.default",
                config='{"headers":{"X-Old":"old"}}',
                env="dev",
            )
        )
    desired = base.CapabilityDesiredStateRepository(db)
    reader = base.BotCapabilityStateReader(
        desired,
        bots,
        skills,
        base.SkillVersionResolver(base.SkillVersionRepository(db)),
    )
    runtime = base._Runtime()
    direct = base.DirectActivationService(
        desired,
        bots,
        base._PersistedSkillQuery(skills),
        runtime,
        base._Authorization(),
        base._Audit(),
        base._Center(),
        reader,
        base.PlatformDefaultMcpPolicy(lambda _: {}),
        MagicMock(),
    )
    activation = base.DeviceActivation(direct)
    uploads = base._PersistedSkillPackages(skills)
    orchestrator = base.ApplyOrchestrator(
        {
            base.ManifestCategory.SKILLS: base.SkillsMaterialiser(
                uploads,
                activation,
                reader,
                base.real_validator(),
                base.DeclaredSourceResolver(
                    base.FakeManifestContent(),
                    base.FakeObjectCredentials(),
                    base.seeded_object_store({}),
                ),
            ),
            base.ManifestCategory.MCP: base.McpMaterialiser(
                activation, base._Center(), base._McpConfig(), reader
            ),
        },
        steps=base.ArcaDelivery(lambda: None).steps_for,
    )
    return db, bots, desired, orchestrator, default_id, set_id


async def apply_case(case, manifest, *, dry=False):
    db, bots, desired, orchestrator, default_id, set_id = case
    bot = bots.get_by_id_and_owner("bot-1", "owner")
    ctx = base._context(bot, apply_id=None if dry else "apply-defaults")
    parsed = base.validate_document(
        "schema_version: 1\napply_policy:\n  skills: {preserve_defaults: true}\n  mcp: {preserve_defaults: true}\nmanifest:\n"
        + manifest,
        ctx.capabilities,
    ).parsed
    assert parsed is not None
    return await orchestrator.apply(
        ctx,
        parsed,
        apply_id="" if dry else "apply-defaults",
        trigger="explicit",
        started_at=datetime.now(),
        dry_run=dry,
    )


@pytest.mark.asyncio
async def test_empty_replace_preserves_default_sources_and_dependencies():
    case = setup_case()
    report = await apply_case(case, "  skills: []\n  mcp: []\n")
    assert report.status is base.ApplyStatus.SUCCEEDED, report
    db, _, _, _, default_id, set_id = case
    with db.orm_session() as session:
        assert {(r.skill_set_id, r.skill_id) for r in session.query(SkillSetSkill)} == {
            (set_id, default_id)
        }
        assert {r.server_code for r in session.query(SkillSetMCPServer)} == {
            "mcp.default"
        }
        assert session.query(DefaultSkillsetSkillExclusion).count() == 0
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0
        assert session.query(BotMCPConfig).count() == 0
        source = session.query(BotSkillInstallationSource).one()
        assert (source.source_kind, source.source_set_id) == ("DEFAULT", set_id)
    mcp = next(c for c in report.categories if c.construct == base.ManifestCategory.MCP)
    assert "mcp.default" not in mcp.removals
    assert any(e.identity == "mcp.default-dep" for e in mcp.entries)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config", ["", "      config: {}\n", "      config: {headers: {X-New: new}}\n"]
)
async def test_explicit_default_mcp_only_replaces_bot_override(config):
    case = setup_case()
    report = await apply_case(case, "  mcp:\n    - server_code: mcp.default\n" + config)
    assert report.status is base.ApplyStatus.SUCCEEDED, report
    db, _, _, _, _, set_id = case
    with db.orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0
        assert (
            session.query(SkillSetMCPServer)
            .filter_by(skill_set_id=set_id, server_code="mcp.default")
            .count()
            == 1
        )
        override = (
            session.query(BotMCPConfig)
            .filter_by(server_code="mcp.default")
            .one_or_none()
        )
        assert (override is not None) == ("X-New" in config)


@pytest.mark.asyncio
async def test_dry_run_explains_default_override_cleanup_without_writing():
    case = setup_case()
    report = await apply_case(case, "  skills: []\n  mcp: []\n", dry=True)
    assert report.status is base.ApplyStatus.SUCCEEDED, report
    entries = [e for c in report.categories for e in c.entries]
    assert any(
        e.identity == "mcp.default"
        and e.outcome == base.EntryOutcome.UPDATED
        and e.note
        for e in entries
    )
    with case[0].orm_session() as session:
        assert (
            session.query(BotMCPConfig).filter_by(server_code="mcp.default").count()
            == 1
        )
        assert session.query(SkillSetSkill).count() == 2


@pytest.mark.asyncio
async def test_mcp_only_does_not_audit_unrelated_unknown_skill_provenance():
    case = setup_case()
    with case[0].transactional_orm_session() as session:
        session.query(BotSkillInstallationSource).delete()
    report = await apply_case(case, "  mcp: []\n")
    assert report.status is base.ApplyStatus.SUCCEEDED, report


@pytest.mark.asyncio
async def test_unknown_skill_provenance_rejects_skills_without_removing_members():
    case = setup_case()
    with case[0].transactional_orm_session() as session:
        session.query(BotSkillInstallationSource).delete()
    report = await apply_case(case, "  skills: []\n")
    assert report.status is not base.ApplyStatus.SUCCEEDED
    with case[0].orm_session() as session:
        assert session.query(SkillSetSkill).count() == 2
        assert session.query(DefaultSkillsetSkillExclusion).count() == 0


@pytest.mark.asyncio
async def test_excluded_default_mcp_explicit_direct_keeps_exclusion():
    case = setup_case()
    with case[0].transactional_orm_session() as session:
        session.add(
            DefaultSkillsetMcpExclusion(
                user_id="entity-1",
                bot_id="bot-1",
                skill_set_id=case[5],
                server_code="mcp.default",
            )
        )
    report = await apply_case(case, "  mcp:\n    - server_code: mcp.default\n")
    assert report.status is base.ApplyStatus.SUCCEEDED, report
    with case[0].orm_session() as session:
        assert session.query(DefaultSkillsetMcpExclusion).count() == 1
        assert (
            session.query(base.BotMCPInstallation)
            .filter_by(server_code="mcp.default")
            .count()
            == 1
        )


def test_transactional_guard_rejects_changed_default_before_config_write():
    from agentclaw.community.core.skill_center.manifest_defaults import (
        ManifestDefaultsConflictError,
    )

    case = setup_case()
    db, _, desired, _, _, set_id = case
    snapshot = desired.manifest_defaults(
        bot_id="bot-1",
        owner_id="entity-1",
        platform_default_codes=frozenset(),
        engine_type="moltis",
        check_skill_sources=False,
    )
    with db.transactional_orm_session() as session:
        session.add(
            SkillSetMCPServer(
                skill_set_id=set_id,
                server_code="mcp.new-default",
                name="new",
                env="dev",
            )
        )
    with pytest.raises(ManifestDefaultsConflictError, match="changed"):
        desired.claim_manifest_mcp(
            bot_id="bot-1",
            owner_id="entity-1",
            server_code="mcp.default",
            config={"headers": {"X-New": "new"}},
            platform_default_codes=frozenset(),
            engine_type="moltis",
            expected_defaults=snapshot,
        )
    with db.orm_session() as session:
        assert (
            "X-Old"
            in session.query(BotMCPConfig)
            .filter_by(server_code="mcp.default")
            .one()
            .config
        )
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0


@pytest.mark.parametrize(
    "policy",
    [
        "null",
        "[]",
        "{cli_tools: {preserve_defaults: true}}",
        "{skills: null}",
        "{skills: {preserve_defaults: 1}}",
        "{skills: {preserve_defaults: 'true'}}",
        "{skills: {other: true}}",
    ],
)
def test_policy_is_closed_and_strict(policy):
    ctx = base._context({}, apply_id=None)
    from agentclaw.community.core.bot_config_manifest.schema.violations import (
        ManifestValidationError,
    )

    with pytest.raises(ManifestValidationError):
        base.validate_document(
            f"schema_version: 1\napply_policy: {policy}\nmanifest: {{}}\n",
            ctx.capabilities,
        )


@pytest.mark.asyncio
async def test_effective_default_skill_name_rejected_before_upload():
    from dataclasses import replace
    from agentclaw.community.core.bot_config_manifest.apply.registry import Intent
    from agentclaw.community.core.skill_center.manifest_defaults import (
        ManifestDefaultsConflictError,
    )

    case = setup_case()
    bot = case[1].get_by_id_and_owner("bot-1", "owner")
    ctx = replace(
        base._context(bot, apply_id="collision"),
        preserve_default_categories=frozenset({base.ManifestCategory.SKILLS}),
    )
    materialiser = case[3]._materialisers[base.ManifestCategory.SKILLS]
    with pytest.raises(ManifestDefaultsConflictError, match="conflicts"):
        await materialiser.plan(
            ctx, (Intent("default-tool", None), Intent("other", None))
        )
    assert materialiser._uploads.packages == {}
    with case[0].orm_session() as session:
        assert session.query(SkillSetSkill).count() == 2


@pytest.mark.asyncio
async def test_policy_alone_does_not_replace_omitted_category():
    case = setup_case()
    await apply_case(case, "  identity: []\n")
    with case[0].orm_session() as session:
        assert session.query(SkillSetSkill).count() == 2
        assert session.query(SkillSetMCPServer).count() == 3


def test_capability_discovery_advertises_only_supported_categories():
    ctx = base._context({}, apply_id=None)
    assert ctx.capabilities.as_payload()["apply_policy_support"] == {
        "skills": ["preserve_defaults"],
        "mcp": ["preserve_defaults"],
    }
    unsupported = base.resolve_capabilities(
        active_engine="moltis", bot_type="desktop", is_teclaw=lambda _: False
    )
    assert unsupported.as_payload()["apply_policy_support"] == {}


def test_http_capabilities_payload_preserves_policy_discovery():
    from agentclaw.community.adapters.http.openapi_v1.bots.config_manifest_support import (
        capabilities_payload,
    )

    ctx = base._context({}, apply_id=None)
    payload = capabilities_payload("bot-1", ctx.capabilities)
    assert payload.apply_policy_support == ctx.capabilities.apply_policy_support()


@pytest.mark.asyncio
async def test_historical_exclusion_also_suppresses_default_membership():
    case = setup_case()
    with case[0].transactional_orm_session() as session:
        session.add(
            DefaultSkillsetMcpExclusion(
                user_id="entity-1",
                bot_id="bot-1",
                skill_set_id=99999,
                server_code="mcp.default",
            )
        )
    report = await apply_case(case, "  mcp:\n    - server_code: mcp.default\n")
    assert report.status is base.ApplyStatus.SUCCEEDED, report
    with case[0].orm_session() as session:
        assert (
            session.query(base.BotMCPInstallation)
            .filter_by(server_code="mcp.default")
            .count()
            == 1
        )
        assert (
            session.query(DefaultSkillsetMcpExclusion)
            .filter_by(skill_set_id=99999)
            .count()
            == 1
        )
        assert (
            session.query(DefaultSkillsetMcpExclusion)
            .filter_by(skill_set_id=case[5])
            .count()
            == 1
        )


@pytest.mark.asyncio
async def test_invalid_source_set_rejects_skills_before_replace():
    case = setup_case()
    with case[0].transactional_orm_session() as session:
        session.query(BotSkillInstallationSource).update({"source_set_id": 99999})
    report = await apply_case(case, "  skills: []\n")
    assert report.status is not base.ApplyStatus.SUCCEEDED
    with case[0].orm_session() as session:
        assert session.query(SkillSetSkill).count() == 2


@pytest.mark.asyncio
async def test_default_added_between_plan_and_engine_publication_stops_bytes():
    from agentclaw.community.core.skill_center.manifest_defaults import (
        ManifestDefaultsConflictError,
    )
    from ...skill_center import test_local_skill_upload_service as upload

    case = setup_case()
    db, _, desired, _, _, set_id = case
    snapshot = desired.manifest_defaults(
        bot_id="bot-1",
        owner_id="entity-1",
        platform_default_codes=frozenset(),
        engine_type="moltis",
    )
    guard = desired.manifest_skill_package_guard(
        bot_id="bot-1",
        owner_id="entity-1",
        name="dependent",
        expected_defaults=snapshot,
        engine_type="moltis",
    )
    package_runtime = upload._PackageRuntime()
    service = upload._service(upload._Filesystem(), package_runtime=package_runtime)
    original = service._same_name_matches

    def interleave(**kwargs):
        with db.transactional_orm_session() as session:
            old_id = (
                session.query(SkillSetSkill)
                .filter(SkillSetSkill.skill_set_id != set_id)
                .one()
                .skill_id
            )
            session.add(SkillSetSkill(skill_set_id=set_id, skill_id=old_id, env="dev"))
        return original(**kwargs)

    service._same_name_matches = interleave
    with pytest.raises(ManifestDefaultsConflictError):
        await service.upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=upload._zip({"SKILL.md": upload._skill_md("dependent")}),
            write_guard=guard,
        )
    assert package_runtime.calls == []


@pytest.mark.asyncio
async def test_blocked_restore_preserves_confirmed_package_partial():
    from contextlib import contextmanager
    from agentclaw.community.core.skill_center.manifest_defaults import (
        ManifestDefaultsConflictError,
        ManifestPackageWriteCommittedError,
    )
    from ...skill_center import test_local_skill_upload_service as upload

    filesystem = upload._Filesystem(cleanup_results=[True, False, True, True, True])
    canonical = "/private/skills-local/upload-skill"
    filesystem.files[f"{canonical}/SKILL.md"] = b"old"
    old = upload._existing_skill(active=False)
    calls = 0

    @contextmanager
    def guard():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise ManifestDefaultsConflictError("Default changed after publication")
        yield

    with pytest.raises(ManifestPackageWriteCommittedError):
        await upload._replacement_service(
            filesystem,
            upload._ReplacementRepo([old]),
            upload._ReplacementRuntime([True, True]),
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=upload._zip(
                {"SKILL.md": upload._skill_md(description="new description")}
            ),
            write_guard=guard,
        )
    assert filesystem.files[f"{canonical}/SKILL.md"] != b"old"
    assert old["description"] == "new description"


@pytest.mark.asyncio
async def test_blocked_create_cleanup_preserves_confirmed_package_partial():
    from contextlib import contextmanager
    from agentclaw.community.core.skill_center.manifest_defaults import ManifestDefaultsConflictError, ManifestPackageWriteCommittedError
    from ...skill_center import test_local_skill_upload_service as upload
    filesystem = upload._Filesystem()
    class FailingRepo(upload._Repo):
        def create(self, row):
            raise RuntimeError("metadata unavailable")
    calls = 0
    @contextmanager
    def guard():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise ManifestDefaultsConflictError("Default changed after publication")
        yield
    with pytest.raises(ManifestPackageWriteCommittedError):
        await upload._service(filesystem, repo=FailingRepo()).upload_local_skill(bot_id="bot", owner_id="owner", actor_id="owner", package=upload._zip({"SKILL.md": upload._skill_md()}), write_guard=guard)
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"]


def test_platform_policy_without_default_set_preserves_supply_and_clears_override():
    case = setup_case()
    db, _, desired, _, _, _ = case
    with db.transactional_orm_session() as session:
        session.query(SkillSetMCPServer).delete()
        session.query(SkillSetSkill).delete()
        session.query(SkillSet).delete()
    snapshot = desired.manifest_defaults(bot_id="bot-1", owner_id="entity-1", platform_default_codes=frozenset({"mcp.default"}), engine_type="moltis", check_skill_sources=False)
    assert snapshot.mcp_codes == frozenset({"mcp.default"})
    desired.remove_manifest_mcp(bot_id="bot-1", owner_id="entity-1", server_code="mcp.default", platform_default_codes=frozenset({"mcp.default"}), engine_type="moltis", expected_defaults=snapshot)
    with db.orm_session() as session:
        assert session.query(BotMCPConfig).filter_by(server_code="mcp.default").count() == 0
        assert session.query(DefaultSkillsetMcpExclusion).count() == 0


def test_excluded_platform_policy_without_default_set_can_be_direct():
    case = setup_case()
    db, _, desired, _, _, _ = case
    with db.transactional_orm_session() as session:
        session.query(SkillSetMCPServer).delete()
        session.query(SkillSetSkill).delete()
        session.query(SkillSet).delete()
        session.add(DefaultSkillsetMcpExclusion(user_id="entity-1", bot_id="bot-1", skill_set_id=99999, server_code="mcp.default"))
    snapshot = desired.manifest_defaults(bot_id="bot-1", owner_id="entity-1", platform_default_codes=frozenset({"mcp.default"}), engine_type="moltis", check_skill_sources=False)
    assert not snapshot.mcp_codes
    desired.claim_manifest_mcp(bot_id="bot-1", owner_id="entity-1", server_code="mcp.default", config=None, platform_default_codes=frozenset({"mcp.default"}), engine_type="moltis", expected_defaults=snapshot)
    with db.orm_session() as session:
        assert session.query(base.BotMCPInstallation).filter_by(server_code="mcp.default").count() == 1
        assert session.query(DefaultSkillsetMcpExclusion).count() == 1


@pytest.mark.asyncio
async def test_empty_policy_rejection_explains_category_in_existing_notes():
    case = setup_case()
    with case[0].transactional_orm_session() as session:
        session.query(BotSkillInstallationSource).delete()
    report = await apply_case(case, "  skills: []\n", dry=True)
    assert report.status is not base.ApplyStatus.SUCCEEDED
    assert any("skills" in note and "safely" in note for note in report.notes)
