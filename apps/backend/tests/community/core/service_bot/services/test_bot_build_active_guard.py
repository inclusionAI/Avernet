"""A publish build marks the source-reading window for the restart guard."""
import threading
from unittest.mock import MagicMock, Mock

import pytest

from agentclaw.community.core.bot_management.engines.artifact_transfer import (
    ArtifactTransferRegistry,
)
from agentclaw.community.core.service_bot.services import bot_build_service as module
from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildMigrationError,
    BotBuildService,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan


def _plan():
    return EngineBuildPlan(
        engine_type="aicoding",
        source_root_name=".aicoding",
        migration_subpath="aicoding",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=[],
        extra_sync_source_relpath=".claude",
        extra_sync_target_relpath="claude",
    )


class FakeActiveRepo:
    """Records the marker lifecycle and exposes what the build test needs."""

    def __init__(self, token="token-1"):
        self.token_result = token
        self.active = False
        self.marked = []
        self.cleared = []
        self.touched = []

    def mark_active(self, env, entity_id, bot_id, ttl_seconds=None):
        self.marked.append((env, entity_id, bot_id))
        self.active = self.token_result is not None
        return self.token_result

    def clear(self, env, entity_id, bot_id, token):
        self.cleared.append((env, entity_id, bot_id, token))
        self.active = False
        return True

    def touch(self, env, entity_id, bot_id, token):
        self.touched.append((env, entity_id, bot_id, token))
        return True


def _service(active_repo, migrate):
    provider = MagicMock()
    provider.get_build_plan.return_value = _plan()
    registry = MagicMock()
    registry.resolve.return_value = provider
    svc = BotBuildService(
        user_list_repository=MagicMock(exists=MagicMock(return_value=False)),
        artifact_transfers=ArtifactTransferRegistry(
            {"aicoding": MagicMock(publish=Mock(return_value=False))}
        ),
        build_ignore_repository=MagicMock(get=MagicMock(return_value=None)),
        env="test",
        device_service=MagicMock(),
        baas_service=MagicMock(),
        path_factory=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        sandbox_registry=registry,
        channel_service=MagicMock(),
        bot_repository=MagicMock(),
        common_whitelist_service=MagicMock(),
        baas_template_resolver=MagicMock(),
        teclaw_template_uuid="teclaw-template",
        build_active_repository=active_repo,
    )
    svc._resolve_sandbox_provider = MagicMock(return_value=provider)
    svc._migrate_bot_instance = migrate
    svc._get_migration_path_base = MagicMock(return_value="/migration/base")
    return svc


@pytest.fixture
def paths(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "get_bot_nas_dir", lambda **kwargs: tmp_path / "nas")
    monkeypatch.setattr(module, "get_bot_dir", lambda **kwargs: tmp_path / "bot")
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)


def _bot():
    return {
        "bot_id": "bot_1",
        "entity_id": "ent_1",
        "entity_type": "staff",
        "active_engine": "aicoding",
    }


def test_build_marks_active_for_the_transfer_and_clears_after(paths):
    repo = FakeActiveRepo()

    def migrate(**_kwargs):
        assert repo.active is True, "transfer must run while the marker is set"
        return True

    svc = _service(repo, migrate)

    result = svc.build(bot=_bot(), version=2)

    assert result["success"] is True
    assert repo.marked == [("test", "ent_1", "bot_1")]
    assert repo.cleared == [("test", "ent_1", "bot_1", "token-1")]
    assert repo.active is False


def test_build_clears_marker_when_migration_raises(paths):
    repo = FakeActiveRepo()

    def migrate(**_kwargs):
        raise BotBuildMigrationError("rsync migration failed")

    svc = _service(repo, migrate)

    with pytest.raises(BotBuildMigrationError):
        svc.build(bot=_bot(), version=2)

    assert repo.cleared == [("test", "ent_1", "bot_1", "token-1")]
    assert repo.active is False


def test_build_does_not_clear_a_marker_it_does_not_own(paths):
    # Another build of the same Bot already owns the marker (token None).
    repo = FakeActiveRepo(token=None)
    svc = _service(repo, Mock(return_value=True))

    result = svc.build(bot=_bot(), version=2)

    assert result["success"] is True
    assert repo.marked == [("test", "ent_1", "bot_1")]
    assert repo.cleared == []
    # The concurrent build's marker is left alone for its owner to clear.


def test_build_without_guard_still_builds(paths):
    svc = _service(None, Mock(return_value=True))

    result = svc.build(bot=_bot(), version=2)

    assert result["success"] is True


def test_heartbeat_renews_the_marker_until_stopped():
    touched = threading.Event()

    class Repo:
        def touch(self, *args):
            touched.set()
            return True

    heartbeat = module._BuildActiveHeartbeat(Repo(), "test", "ent_1", "bot_1", "tok", 0.01)
    heartbeat.start()
    try:
        assert touched.wait(2.0), "heartbeat never renewed the marker"
    finally:
        heartbeat.stop()