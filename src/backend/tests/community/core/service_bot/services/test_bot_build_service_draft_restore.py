
import subprocess
from unittest.mock import MagicMock

import pytest

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
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)

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
    for command_call in svc._run_local_command.call_args_list:
        assert command_call.kwargs["timeout_seconds"] == 1800


def test_run_local_command_reports_timeout(monkeypatch):
    svc = object.__new__(BotBuildService)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        MagicMock(side_effect=subprocess.TimeoutExpired(cmd="rsync", timeout=1800)),
    )

    with pytest.raises(
        module.BotBuildMigrationError,
        match="restore draft workspace failed: command timed out after 1800 seconds",
    ):
        svc._run_local_command(
            cmd=["rsync"],
            command_name="rsync draft restore",
            error_message="restore draft workspace failed",
            timeout_seconds=1800,
        )


@pytest.mark.asyncio
async def test_restore_teclaw_draft_submits_then_checks_progress_one_step_at_a_time(
    monkeypatch,
):
    baas = MagicMock()
    baas.update_teclaw_bot.return_value = {
        "bot_uuid": "BOT-current",
        "publish_id": 901,
    }
    baas.get_publish_progress.side_effect = [
        {"status": "ACTIVE"},
        {"status": "SUCCESS"},
    ]
    svc = BotBuildService(
        device_service=MagicMock(),
        baas_service=baas,
        path_factory=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        sandbox_registry=MagicMock(),
        channel_service=MagicMock(),
        bot_repository=MagicMock(),
        common_whitelist_service=MagicMock(),
        baas_template_resolver=MagicMock(),
        teclaw_template_uuid="teclaw-template",
    )
    monkeypatch.setattr(
        module.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "nonce"})()
    )
    svc.generate_request_id = MagicMock(return_value="request-id")
    historical = {
        "schema_version": 4,
        "engine_type": "teclaw",
        "engine_ext": {"stage": "release", "opaque": True},
        "engine_overrides": {
            "channels": {"dingding": {"enabled": True}},
            "other": "keep",
        },
        "resources": [{"name": "old.txt", "store": "bot-data", "path": "old"}],
    }
    submitted = await svc.restore_teclaw_draft_async(
        bot_uuid="BOT-current",
        bot={"bot_id": "b1", "entity_id": "u1", "active_engine": "teclaw"},
        owner_id="u1",
        source_version=3,
        artifact_ext={"config_artifact": historical},
    )

    assert submitted == {
        "restore_type": "config_artifact",
        "publish_id": 901,
        "bot_uuid": "BOT-current",
        "baas_status": "SUBMITTED",
        "status": "restoring",
    }
    svc.generate_request_id.assert_called_once_with(
        bot={"bot_id": "b1", "entity_id": "u1", "active_engine": "teclaw"},
        publish_stage="draft_restore_3_nonce",
    )
    kwargs = baas.update_teclaw_bot.call_args.kwargs
    delivered = kwargs["config_artifact"]
    assert kwargs["bot_uuid"] == "BOT-current"
    assert kwargs["owner_id"] == "u1"
    assert kwargs["template_uuid"] == "teclaw-template"
    assert delivered["engine_ext"]["stage"] == "draft"
    assert delivered["engine_ext"]["opaque"] is True
    assert delivered["engine_overrides"] == {"other": "keep"}
    assert historical["engine_ext"]["stage"] == "release"
    assert "channels" in historical["engine_overrides"]
    active = await svc.restore_teclaw_draft_async(
        bot_uuid="BOT-current",
        bot={"bot_id": "b1", "entity_id": "u1", "active_engine": "teclaw"},
        owner_id="u1",
        source_version=3,
        artifact_ext={"config_artifact": historical},
        baas_publish_id=901,
    )
    assert active["status"] == "restoring"
    assert active["baas_status"] == "ACTIVE"

    completed = await svc.restore_teclaw_draft_async(
        bot_uuid="BOT-current",
        bot={"bot_id": "b1", "entity_id": "u1", "active_engine": "teclaw"},
        owner_id="u1",
        source_version=3,
        artifact_ext={"config_artifact": historical},
        baas_publish_id=901,
    )
    assert completed == {
        "restore_type": "config_artifact",
        "baas_publish_id": 901,
        "baas_status": "SUCCESS",
        "status": "success",
    }
    assert baas.update_teclaw_bot.call_count == 1
    assert baas.get_publish_progress.call_args_list == [((901, True),), ((901, True),)]


@pytest.mark.asyncio
async def test_restore_teclaw_draft_reports_terminal_baas_failure():
    baas = MagicMock()
    baas.update_teclaw_bot.return_value = {"publish_id": 902}
    baas.get_publish_progress.return_value = {"status": "FAILED"}
    svc = BotBuildService(
        device_service=MagicMock(),
        baas_service=baas,
        path_factory=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        sandbox_registry=MagicMock(),
        channel_service=MagicMock(),
        bot_repository=MagicMock(),
        common_whitelist_service=MagicMock(),
        baas_template_resolver=MagicMock(),
        teclaw_template_uuid="teclaw-template",
    )

    result = await svc.restore_teclaw_draft_async(
        bot_uuid="BOT-current",
        bot={"bot_id": "b1", "entity_id": "u1", "active_engine": "teclaw"},
        owner_id="u1",
        source_version=3,
        artifact_ext={
            "config_artifact": {
                "schema_version": 4,
                "engine_type": "teclaw",
                "engine_ext": {"stage": "release"},
            }
        },
        baas_publish_id=902,
    )

    assert result == {
        "restore_type": "config_artifact",
        "baas_publish_id": 902,
        "baas_status": "FAILED",
        "status": "failed",
        "error": "teclaw 草稿热更新失败: publish_id=902, status=FAILED",
    }
    baas.update_teclaw_bot.assert_not_called()


@pytest.mark.asyncio
async def test_restore_teclaw_draft_retries_after_progress_query_error():
    baas = MagicMock()
    baas.get_publish_progress.side_effect = RuntimeError("temporary gateway error")
    svc = BotBuildService(
        device_service=MagicMock(),
        baas_service=baas,
        path_factory=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        sandbox_registry=MagicMock(),
        channel_service=MagicMock(),
        bot_repository=MagicMock(),
        common_whitelist_service=MagicMock(),
        baas_template_resolver=MagicMock(),
        teclaw_template_uuid="teclaw-template",
    )

    result = await svc.restore_teclaw_draft_async(
        bot_uuid="BOT-current",
        bot={"bot_id": "b1", "entity_id": "u1", "active_engine": "teclaw"},
        owner_id="u1",
        source_version=3,
        artifact_ext={
            "config_artifact": {
                "schema_version": 4,
                "engine_type": "teclaw",
                "engine_ext": {"stage": "release"},
            }
        },
        baas_publish_id=902,
    )

    assert result["status"] == "restoring"
    assert result["baas_status"] == "QUERY_ERROR"
    assert result["progress_error"] == "temporary gateway error"
    baas.update_teclaw_bot.assert_not_called()
