
from unittest.mock import MagicMock

from agentclaw.community.core.service_bot.services import bot_build_service as module
from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildService
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan


def test_restore_draft_arca_rsyncs_versioned_artifact_into_draft_nas(monkeypatch, tmp_path):
    plan = EngineBuildPlan(
        engine_type="openclaw",
        source_root_name=".openclaw",
        migration_subpath="openclaw",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=["agents/*/sessions"],
    )
    provider = MagicMock()
    provider.get_build_plan.return_value = plan
    registry = MagicMock()
    registry.resolve.return_value = provider
    baas = MagicMock()
    baas.resolve_container_provider.return_value = "baas"
    svc = BotBuildService(
        device_service=MagicMock(),
        baas_service=baas,
        path_factory=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        sandbox_registry=registry,
        channel_service=MagicMock(),
        bot_repository=MagicMock(),
        common_whitelist_service=MagicMock(),
        baas_template_resolver=MagicMock(),
        teclaw_template_uuid="teclaw-template",
    )
    svc._run_local_command = MagicMock()

    bot_root = tmp_path / "bot-data"
    artifact_dir = bot_root / "3" / "openclaw"
    artifact_dir.mkdir(parents=True)
    nas_root = tmp_path / "draft-nas"
    nas_root.mkdir()
    monkeypatch.setattr(module, "get_bot_dir", lambda *args, **kwargs: bot_root)
    monkeypatch.setattr(module, "get_bot_nas_dir", lambda *args, **kwargs: nas_root)

    result = svc.restore_draft(
        bot={
            "bot_id": "b1",
            "entity_id": "u1",
            "entity_type": "staff",
            "active_engine": "openclaw",
        },
        source_version=3,
        artifact_ext={"migration_path": "/home/admin/nfs/bot-data/3/openclaw"},
    )

    assert result["restore_type"] == "migration_path"
    assert result["artifact_path"] == str(artifact_dir)
    rsync_call = svc._run_local_command.call_args_list[1].kwargs["cmd"]
    assert rsync_call[:4] == ["sudo", "rsync", "-av", "--delete"]
    assert "--exclude=agents/*/sessions" in rsync_call
    assert rsync_call[-2:] == [f"{artifact_dir}/", f"{nas_root / '.openclaw'}/"]
