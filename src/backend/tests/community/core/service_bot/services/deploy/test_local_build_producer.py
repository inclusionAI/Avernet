"""Local publishing must snapshot the selected Bot, never the global engine root."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
)
from agentclaw.community.core.service_bot.services.deploy.local_build_producer import (
    LocalBuildProducer,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.core.workspace.engines.openclaw import OpenClawSandboxProvider
from agentclaw.community.core.workspace.engines.hermes import HermesSandboxProvider
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di.config import WorkspaceConfig


class _SnapshotBuildService:
    """Keep source selection real without remote stage configuration or sudo."""

    def __init__(self, artifacts: Path) -> None:
        self.artifacts = artifacts

    def build(self, *, bot, version, local_source_root):
        target = self.artifacts / bot["bot_id"] / str(version)
        shutil.copytree(local_source_root, target)
        return {"success": True, "build_target_path": str(target)}


@pytest.fixture
def local_build(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDESKTOP_ROOT", str(tmp_path / "aidesktop"))
    monkeypatch.setenv("LOCAL_AIDESKTOP_ROOT", str(tmp_path / "aidesktop"))
    monkeypatch.setattr(
        "agentclaw.community.core.workspace.path_factory._get_aidesktop_env_folder",
        lambda: "aidesktop_singlebox",
    )
    global_root = tmp_path / "global-openclaw"
    global_root.mkdir()
    (global_root / "identity.md").write_text("global, not a Bot")
    registry = EngineSandboxRegistry()
    registry.register(
        OpenClawSandboxProvider(WorkspaceConfig(openclaw_root=str(global_root)))
    )
    registry.register(
        HermesSandboxProvider(WorkspaceConfig(hermes_root=str(global_root)))
    )
    producer = LocalBuildProducer(
        build_service=_SnapshotBuildService(tmp_path / "artifacts"),
        skills_manifest_builder=Mock(capture=Mock(return_value=None)),
        sandbox_registry=registry,
        path_factory=WorkspacePathFactory(skill_repo_sync=Mock()),
    )
    return producer, tmp_path / "aidesktop/aidesktop_singlebox/bolt_data"


def _bot(bot_id="bot-a", entity_id="owner-a", entity_type="staff"):
    return {
        "bot_id": bot_id,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "active_engine": "openclaw",
    }


def _publish(producer, bot):
    return producer.produce_artifact(ArtifactBuildRequest.create(bot=bot, version=1))


@pytest.mark.parametrize(
    "bot_id,entity_id,entity_type,relative_root",
    [
        ("bot-a", "owner-a", "staff", "staff_owner-a/bot-a/openclaw"),
        ("bot-b", "owner-a", "staff", "staff_owner-a/bot-b/openclaw"),
        ("bot-a", "team-a", "team", "team_team-a/bot-a/openclaw"),
    ],
)
def test_snapshots_only_the_requested_bot(
    local_build,
    bot_id,
    entity_id,
    entity_type,
    relative_root,
):
    producer, base = local_build
    source = base / relative_root
    source.mkdir(parents=True)
    (source / "identity.md").write_text(relative_root)
    artifact = _publish(producer, _bot(bot_id, entity_id, entity_type))
    assert artifact.success
    assert (
        Path(artifact.ext["build_target_path"]) / "identity.md"
    ).read_text() == relative_root


def test_missing_bot_source_does_not_fall_back_to_existing_global_root(local_build):
    producer, _ = local_build
    with pytest.raises(ValueError, match="local build source directory"):
        _publish(producer, _bot())


@pytest.mark.parametrize("field", ["bot_id", "entity_id", "entity_type"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_identity_is_rejected_before_build(local_build, field, value):
    producer, _ = local_build
    bot = _bot()
    bot[field] = value
    with pytest.raises(ValueError, match=field):
        _publish(producer, bot)


def test_two_bots_keep_independent_snapshot_contents(local_build):
    producer, base = local_build
    for bot_id, content in (("bot-a", "first Bot"), ("bot-b", "second Bot")):
        source = base / f"staff_owner-a/{bot_id}/openclaw"
        source.mkdir(parents=True)
        (source / "identity.md").write_text(content)
    first = _publish(producer, _bot("bot-a"))
    second = _publish(producer, _bot("bot-b"))
    first_path = Path(first.ext["build_target_path"])
    second_path = Path(second.ext["build_target_path"])
    assert first_path != second_path
    assert (first_path / "identity.md").read_text() == "first Bot"
    assert (second_path / "identity.md").read_text() == "second Bot"


def test_oss_view_source_is_translated_to_local_host(local_build, monkeypatch):
    producer, base = local_build
    monkeypatch.setenv("AIDESKTOP_ROOT", "/aidesktop")
    monkeypatch.setattr(
        "agentclaw.community.utils.env_utils.is_local_mode", lambda: True
    )
    source = base / "staff_owner-a/bot-a/openclaw"
    source.mkdir(parents=True)
    (source / "identity.md").write_text("host source")
    artifact = _publish(producer, _bot())
    assert (
        Path(artifact.ext["build_target_path"]) / "identity.md"
    ).read_text() == "host source"


def test_source_file_is_not_accepted_as_engine_directory(local_build):
    producer, base = local_build
    source = base / "staff_owner-a/bot-a/openclaw"
    source.parent.mkdir(parents=True)
    source.write_text("not a directory")
    with pytest.raises(ValueError, match="local build source directory"):
        _publish(producer, _bot())


def test_singlebox_di_constructs_local_producer_with_path_factory(local_build):
    from agentclaw.community.core.service_bot.services.deploy.producer import (
        DeployArtifactProducerRouter,
    )
    from agentclaw.community.di.container import build_injector
    from agentclaw.community.di.profile import DeployProfile

    _, base = local_build
    source = base / "staff_owner-a/bot-a/openclaw"
    source.mkdir(parents=True)
    injector = build_injector(profile=DeployProfile.SINGLEBOX)
    router = injector.get(DeployArtifactProducerRouter)
    producer = router.resolve("baas")
    assert isinstance(producer, LocalBuildProducer)
    assert producer._resolve_local_source_root(_bot()) == source


def test_uses_the_selected_engine_directory(local_build):
    producer, base = local_build
    source = base / "staff_owner-a/bot-a/hermes"
    source.mkdir(parents=True)
    (source / "identity.md").write_text("Hermes Bot")
    bot = _bot()
    bot["active_engine"] = "hermes"
    artifact = _publish(producer, bot)
    assert (
        Path(artifact.ext["build_target_path"]) / "identity.md"
    ).read_text() == "Hermes Bot"


def test_omitted_entity_type_keeps_staff_default(local_build):
    producer, base = local_build
    source = base / "staff_owner-a/bot-a/openclaw"
    source.mkdir(parents=True)
    (source / "identity.md").write_text("staff Bot")
    bot = _bot()
    del bot["entity_type"]
    artifact = _publish(producer, bot)
    assert (
        Path(artifact.ext["build_target_path"]) / "identity.md"
    ).read_text() == "staff Bot"


@pytest.mark.parametrize("engine", ["openclaw", "hermes"])
def test_local_producer_builds_real_snapshot_without_remote_services(
    local_build, monkeypatch, engine
):
    """Exercise the real build and rsync path, including its local-only branches."""
    from agentclaw.community.core.service_bot.services import bot_build_service

    producer, base = local_build
    source = base / f"staff_owner-a/bot-a/{engine}"
    source.mkdir(parents=True)
    (source / "identity.md").write_text("selected Bot")
    (source / "logs").mkdir()
    (source / "logs/runtime.log").write_text("not a deployable file")
    (source / "openclaw.json").write_text('{"name": "selected Bot"}')
    if engine == "openclaw":
        (source / "openclaw.json.asback_123").write_text("transient backup")

    def unexpected_remote_call(*args, **kwargs):
        pytest.fail("local snapshot must not access NAS, MCP generation, or remote sync")

    service = bot_build_service.BotBuildService.__new__(bot_build_service.BotBuildService)
    service._sandbox_registry = producer._sandbox_registry
    service._device_service = Mock(exec_shell_new=unexpected_remote_call)
    monkeypatch.setattr(bot_build_service, "get_bot_nas_dir", unexpected_remote_call)
    monkeypatch.setattr(service, "_generate_mcp_config", unexpected_remote_call)
    # Stage configuration belongs to the channel service, not the snapshot copy.
    monkeypatch.setattr(service, "_generate_openclaw_stage_configs", lambda **kwargs: True)
    monkeypatch.setattr(
        service, "_get_migration_path_base", lambda **kwargs: "/artifacts/bot-a"
    )
    producer._build_service = service
    bot = _bot()
    bot.update(active_engine=engine, device_id="local-device")

    artifact = _publish(producer, bot)

    assert artifact.success
    target = base / f"staff_owner-a/bot-a/1/{engine}"
    assert Path(artifact.ext["build_target_path"]) == target
    assert artifact.ext["migration_path"] == f"/artifacts/bot-a/1/{engine}"
    assert (target / "identity.md").read_text() == "selected Bot"
    assert (target / "openclaw.json").read_text() == '{"name": "selected Bot"}'
    assert not (target / "logs").exists()
    assert not (target / "openclaw.json.asback_123").exists()


def test_local_producer_rejects_missing_publish_version(local_build):
    producer, _ = local_build
    request = ArtifactBuildRequest.create(bot=_bot(), version=None)
    with pytest.raises(ValueError, match="requires a publish version"):
        producer.produce_artifact(request)
