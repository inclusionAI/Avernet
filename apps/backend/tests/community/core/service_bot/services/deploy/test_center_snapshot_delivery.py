"""Exercise shadowed Center skills in file snapshots and release replay."""

from dataclasses import replace
from os.path import relpath
from pathlib import Path
from shutil import copytree
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
    ArcaSnapshotProducer,
)
from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    ServiceSkillsManifestBuilder,
    ServiceSkillsManifestError,
    validate_service_skills_manifest_for_release,
)
from agentclaw.community.core.service_bot.services.service_artifact_refs import (
    exact_center_refs_from_artifact_ext,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import MAPPING_CONTRACT_VERSION
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)
from tests.community.core.service_bot.services.deploy.test_arca_snapshot_producer import (
    _CENTER_STORE_PREFIX,
    _REPO_STORE_PREFIX,
    _CapabilityReader,
    _CenterStore,
    _LayoutRepository,
    _ready_observation,
)
from tests.community.core.service_bot.services.test_publish_flow_service import (
    _arca_router,
    _make_publish_record,
    _pf,
)

_UUID = "00000000-0000-4000-8000-000000000001"
_VERSION = "v1.19"
_CONTENT = "# Self-installed\n"


@pytest.fixture(params=["openclaw", "aicoding"])
def snapshot(request, tmp_path):
    engine = request.param
    coding = engine == "aicoding"
    bot = {
        "bot_id": "b1",
        "owner_id": "u1",
        "entity_id": "u1",
        "env": "dev",
        "active_engine": "claude_code" if coding else engine,
        "device_provider": "baas",
    }
    observed = _ready_observation(engine)
    active = "claude/skills" if coding else "workspace/skills"
    draft = tmp_path / "draft"
    (draft / active).mkdir(parents=True)
    entry = draft / active / "observ"
    target = Path(observed.resolved_layout.center_root) / _UUID / _VERSION
    scope = BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1")
    layout = _LayoutRepository(BotSkillLayoutState.legacy_default(scope))
    store = _CenterStore()
    builder = ServiceSkillsManifestBuilder(
        layout,
        _CapabilityReader((
            RegisteredSkillAsset(
                skill_id=1, name="observ", git_path="center://observ",
                skill_uuid=_UUID, sc_version_number=_VERSION,
            ),
        )),
        _CENTER_STORE_PREFIX,
        store,
        _REPO_STORE_PREFIX,
    )

    post_copy = None

    def copy_snapshot(*, bot, version, shared_corpora, active_runtime_path):
        destination = tmp_path / str(version)
        copytree(draft, destination, symlinks=True)
        if post_copy is not None:
            post_copy(destination)
        return {
            "success": True,
            "migration_path": str(destination),
            "build_target_path": str(destination),
            "shared_corpus_snapshot_paths": [
                f"workspace/skills-pool/{'skill-center' if item.corpus == 'center' else 'skills-repo'}"
                for item in shared_corpora
            ],
            "active_skill_snapshot_path": active,
        }

    producer = ArcaSnapshotProducer(Mock(build=copy_snapshot), builder)

    def produce(version, *, center_available=True, after_copy=None):
        nonlocal post_copy
        post_copy = after_copy
        observation = observed if center_available else replace(
            observed,
            center_mount_status="NOT_READY",
            supported_mapping_contract_versions=frozenset({MAPPING_CONTRACT_VERSION}),
        )
        artifact = producer.produce_artifact(
            ArtifactBuildRequest.create(bot=bot, version=version, layout_observation=observation)
        )
        validate_service_skills_manifest_for_release(artifact.ext["skills_manifest"], bot)
        return artifact.ext

    return bot, layout, store, entry, target, produce


def _write_local(entry: Path) -> None:
    entry.mkdir()
    (entry / "SKILL.md").write_text(_CONTENT, encoding="utf-8")


def _snapshot_entry(ext):
    return Path(ext["build_target_path"]) / ext["active_skill_snapshot_path"] / "observ"


@pytest.mark.unit
@pytest.mark.parametrize("pool", [False, True])
def test_snapshot_delivers_local_files_and_pins_only_center_links(snapshot, pool):
    _, layout, store, entry, target, produce = snapshot
    if pool:
        layout.state = replace(
            layout.state, active_layout=SkillLayout.POOL, phase=SkillLayoutPhase.POOL_ACTIVE,
            migration_generation="generation-1", layout_contract_version="skills-pool-p3-v1",
            persisted=True,
        )

    _write_local(entry)
    store.ready = False
    local = produce(1, center_available=False)
    assert exact_center_refs_from_artifact_ext(local) == ()
    assert not store.refs
    assert not _snapshot_entry(local).is_symlink()
    assert (_snapshot_entry(local) / "SKILL.md").read_text() == _CONTENT
    assert (entry / "SKILL.md").read_text() == _CONTENT
    corpora = local["skills_manifest"].get("shared_corpora", [])
    assert [item["corpus"] for item in corpora] == (["repo", "center"] if pool else [])

    (entry / "SKILL.md").unlink()
    entry.rmdir()
    entry.symlink_to(target)
    store.ready = True
    center = produce(2)
    (ref,) = exact_center_refs_from_artifact_ext(center)
    assert (ref.skill_uuid, ref.sc_version_number) == (_UUID, _VERSION)
    assert [ref.identity.sc_version_number for ref in store.refs] == [_VERSION]
    assert _snapshot_entry(center).readlink() == target


@pytest.mark.unit
@pytest.mark.parametrize("invalid", ["missing", "empty-directory", "wrong-version", "external-link"])
def test_snapshot_rejects_incomplete_or_dependent_override(snapshot, invalid):
    _, _, _, entry, target, produce = snapshot
    if invalid == "empty-directory":
        entry.mkdir()
    elif invalid == "wrong-version":
        entry.symlink_to(target.with_name("v2.0"))
    elif invalid == "external-link":
        _write_local(entry)
        (entry.parent / "outside").write_text("outside package")
        (entry / "dependency").symlink_to("../outside")
    with pytest.raises(ServiceSkillsManifestError, match="frozen exact manifest: .*observ"):
        produce(1)


@pytest.mark.unit
@pytest.mark.parametrize("relative", [False, True])
def test_local_override_still_rejects_undeclared_center_alias(snapshot, relative):
    bot, _, _, entry, target, produce = snapshot
    _write_local(entry)
    if relative:
        engine = "aicoding" if bot["active_engine"] == "claude_code" else "openclaw"
        active_runtime = _ready_observation(engine).resolved_layout.active_root
        alias = relpath(target, active_runtime)
    else:
        alias = target.parent.parent / ".." / "skill-center" / _UUID / _VERSION
    (entry.parent / "undeclared").symlink_to(alias)
    with pytest.raises(ServiceSkillsManifestError, match="frozen exact manifest: .*undeclared"):
        produce(1)


@pytest.mark.unit
def test_center_publish_tolerates_an_unreadable_unrelated_directory(snapshot):
    _, _, _, entry, target, produce = snapshot
    entry.symlink_to(f"{target}/")  # Existing directory links can include a trailing slash.
    inaccessible = None

    def make_unreadable(snapshot_root):
        nonlocal inaccessible
        inaccessible = snapshot_root / "unrelated"
        inaccessible.mkdir()
        inaccessible.chmod(0)

    try:
        published = produce(1, after_copy=make_unreadable)
    finally:
        if inaccessible is not None:
            inaccessible.chmod(0o700)
    (ref,) = exact_center_refs_from_artifact_ext(published)
    assert (ref.skill_uuid, ref.sc_version_number) == (_UUID, _VERSION)


@pytest.mark.unit
def test_local_override_rejects_relative_center_link_outside_active_root(snapshot):
    bot, _, _, entry, target, produce = snapshot
    _write_local(entry)
    engine = "aicoding" if bot["active_engine"] == "claude_code" else "openclaw"
    active_runtime = Path(_ready_observation(engine).resolved_layout.active_root)
    runtime_parent = active_runtime.parent / "aliases"
    snapshot_parent = Path("claude" if engine == "aicoding" else "workspace") / "aliases"

    def add_alias(snapshot_root):
        aliases = snapshot_root / snapshot_parent
        aliases.mkdir(parents=True, exist_ok=True)
        (aliases / "hidden-center").symlink_to(relpath(target, runtime_parent))

    with pytest.raises(ServiceSkillsManifestError, match="frozen exact manifest: .*hidden-center"):
        produce(1, after_copy=add_alias)


@pytest.mark.asyncio
@pytest.mark.parametrize("start_local", [False, True])
async def test_online_upgrade_and_rollback_deliver_their_frozen_skill_snapshots(snapshot, start_local):
    bot, _, _, entry, target, produce = snapshot
    ext_by_source = {}
    for version, local in enumerate((start_local, not start_local), start=1):
        if local:
            _write_local(entry)
        else:
            entry.symlink_to(target)
        ext_by_source[local] = produce(version)
        if local:
            (entry / "SKILL.md").unlink()
            entry.rmdir()
        else:
            entry.unlink()

    previous = _make_publish_record(
        id=10, version=1, status=PublishStatus.SUCCESS.value, source_bot_id=bot["bot_id"],
        ext={**ext_by_source[start_local], "binding": {"online": 88}},
    )
    next_record = _make_publish_record(
        id=11, version=2, status=PublishStatus.VALIDATING.value, source_bot_id=bot["bot_id"],
        last_pub_id=10, ext=ext_by_source[not start_local],
    )
    publish_service = Mock()
    publish_service.get_publish_by_id.side_effect = {10: previous, 11: next_record}.get
    publish_service.get_device_binding_by_id.return_value = Mock(device_id="BOT-old")
    build_service = Mock(upgrade_async=AsyncMock(side_effect=[{"publish_id": 20}, {"publish_id": 21}]))
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "arca"
    bot_service = Mock()
    bot_service.get_bot_by_address.return_value = bot
    flow = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    await flow._execute_upgrade_release(
        publish_record=next_record, operator="u1", migration_path=next_record.ext["migration_path"], bot=bot,
    )
    await flow.execute_rollback(current_publish_id=11, target_publish_id=10, operator="u1")

    for call, local in zip(build_service.upgrade_async.await_args_list, (not start_local, start_local), strict=True):
        ext = ext_by_source[local]
        assert call.kwargs["migration_path"] == ext["migration_path"]
        assert call.kwargs["ext_info"] == {"skills_manifest": ext["skills_manifest"]}
        delivered = _snapshot_entry(ext)
        if local:
            assert not delivered.is_symlink()
            assert (delivered / "SKILL.md").read_text() == _CONTENT
        else:
            assert delivered.readlink() == target
