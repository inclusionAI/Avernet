"""Unit tests for SkillPropagationService — Phase 1 骨架验收。

测试覆盖：
  - propagate_on_upgrade / propagate_on_removal 基本调用不抛异常
  - log_repo 抛异常时方法仍返回 PropagationResult（非阻塞）
  - 30s 内重复调用命中幂等缓存，返回相同 propagation_log_id
"""
import json
from unittest.mock import MagicMock


def _make_service(log_repo=None, skill_set_repo=None, resolver=None, device_sync_dispatcher=None, **extra):
    from agentclaw.community.core.skill_center.services.skill_propagation_service import SkillPropagationService
    if log_repo is None:
        log_repo = _mock_log_repo()
    return SkillPropagationService(
        log_repo=log_repo,
        skill_set_repo=skill_set_repo or MagicMock(),
        resolver=resolver or MagicMock(),
        device_sync_dispatcher=device_sync_dispatcher or MagicMock(),
        skill_set_service_factory=MagicMock(),
        **extra,
    )


def _mock_log_repo(propagation_id="test-prop-id", recent=None):
    repo = MagicMock()
    repo.create.return_value = {
        "propagation_id": propagation_id,
        "status": "pending",
        "affected_bot_count": 0,
        "success_bot_count": 0,
        "failed_bot_ids": "[]",
    }
    repo.update.return_value = None
    repo.find_recent.return_value = recent
    return repo


class TestPropagateOnUpgrade:
    def test_smoke_returns_result(self):
        """propagate_on_upgrade 正常调用，返回 PropagationResult，不抛异常。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import PropagationResult
        svc = _make_service()
        result = svc.propagate_on_upgrade(
            skill_uuid="uuid-abc", env="dev", new_version="2.0.0"
        )
        assert isinstance(result, PropagationResult)
        # propagation_log_id 是 service 内部生成的 UUID，只校验非空
        assert result.propagation_log_id is not None
        assert result.affected_bot_count == 0  # Phase 1 骨架

    def test_log_repo_create_called_with_correct_fields(self):
        """应写入正确的 action / skill_uuid / env 到 log。"""
        repo = _mock_log_repo()
        svc = _make_service(log_repo=repo)
        svc.propagate_on_upgrade(skill_uuid="uuid-abc", env="prod", new_version="3.0.0")

        repo.create.assert_called_once()
        call_data = repo.create.call_args[0][0]
        assert call_data["action"] == "upgrade"
        assert call_data["skill_uuid"] == "uuid-abc"
        assert call_data["env"] == "prod"
        assert call_data["status"] == "pending"
        assert json.loads(call_data["extra"])["new_version"] == "3.0.0"

    def test_log_repo_update_called_with_done(self):
        """成功后应将 log 更新为 done 状态。"""
        repo = _mock_log_repo()
        svc = _make_service(log_repo=repo)
        svc.propagate_on_upgrade(skill_uuid="uuid-abc", env="dev", new_version="1.0.0")

        repo.update.assert_called_once()
        call_args = repo.update.call_args
        # 第一个参数是 propagation_id（内部 UUID），不固定；只校验 status
        assert call_args[0][1]["status"] == "done"


class TestPropagateOnRemoval:
    def test_smoke_returns_result(self):
        """propagate_on_removal 正常调用，返回 PropagationResult，不抛异常。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import PropagationResult
        svc = _make_service()
        result = svc.propagate_on_removal(skill_uuid="uuid-xyz", env="pre")
        assert isinstance(result, PropagationResult)

    def test_log_repo_create_called_with_removal_action(self):
        """action 字段应为 removal。"""
        repo = _mock_log_repo()
        svc = _make_service(log_repo=repo)
        svc.propagate_on_removal(skill_uuid="uuid-xyz", env="pre")

        call_data = repo.create.call_args[0][0]
        assert call_data["action"] == "removal"
        assert call_data["skill_uuid"] == "uuid-xyz"


class TestSwallowDbException:
    def test_create_raises_still_returns_result(self):
        """log_repo.create 抛异常时方法仍返回 PropagationResult，不向上抛。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import PropagationResult
        repo = MagicMock()
        repo.find_recent.return_value = None
        repo.create.side_effect = RuntimeError("DB connection failed")
        repo.update.return_value = None

        svc = _make_service(log_repo=repo)
        result = svc.propagate_on_upgrade(
            skill_uuid="uuid-err", env="dev", new_version="1.0.0"
        )
        assert isinstance(result, PropagationResult)
        assert result.affected_bot_count == 0
        assert result.failed_bot_ids == []

    def test_update_raises_still_returns_result(self):
        """log_repo.update 抛异常时方法仍返回 PropagationResult。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import PropagationResult
        repo = _mock_log_repo()
        repo.update.side_effect = RuntimeError("update failed")

        svc = _make_service(log_repo=repo)
        result = svc.propagate_on_upgrade(
            skill_uuid="uuid-err2", env="dev", new_version="1.0.0"
        )
        assert isinstance(result, PropagationResult)


class TestIdempotentWithinWindow:
    def test_second_call_returns_cached_log_id(self):
        """30s 内同 (skill_uuid, env) 第二次调用应复用上次的 propagation_log_id。"""
        cached_log = {
            "propagation_id": "cached-id-999",
            "status": "done",
            "affected_bot_count": 2,
            "success_bot_count": 2,
            "failed_bot_ids": "[]",
        }
        repo = _mock_log_repo(recent=cached_log)
        svc = _make_service(log_repo=repo)

        result = svc.propagate_on_upgrade(
            skill_uuid="uuid-dup", env="prod", new_version="2.0.0"
        )
        assert result.propagation_log_id == "cached-id-999"
        assert result.affected_bot_count == 2
        # create 不应被调用（直接复用缓存）
        repo.create.assert_not_called()

    def test_idempotent_hit_on_pending_status(self):
        """pending 状态的记录也应命中幂等（避免重复提交）。"""
        cached_log = {
            "propagation_id": "pending-id-111",
            "status": "pending",
            "affected_bot_count": 0,
            "success_bot_count": 0,
            "failed_bot_ids": "[]",
        }
        repo = _mock_log_repo(recent=cached_log)
        svc = _make_service(log_repo=repo)
        result = svc.propagate_on_removal(skill_uuid="uuid-dup", env="dev")
        assert result.propagation_log_id == "pending-id-111"
        repo.create.assert_not_called()

    def test_failed_status_not_idempotent(self):
        """failed 状态的历史记录不应阻止重试（不命中幂等）。"""
        cached_log = {
            "propagation_id": "failed-id-222",
            "status": "failed",
            "affected_bot_count": 0,
            "success_bot_count": 0,
            "failed_bot_ids": "[]",
        }
        repo = _mock_log_repo(propagation_id="new-id-333", recent=cached_log)
        # find_recent 返回 failed 记录时，service 内部要重新执行
        repo.find_recent.return_value = cached_log
        svc = _make_service(log_repo=repo)
        result = svc.propagate_on_upgrade(
            skill_uuid="uuid-retry", env="dev", new_version="1.0.0"
        )
        # 应创建新记录
        repo.create.assert_called_once()
        # propagation_log_id 是 service 内部生成的 UUID，不等于 mock 返回值
        assert result.propagation_log_id is not None
        assert result.propagation_log_id != "failed-id-222"


class TestFindAffectedBotsReturnsEngineType:
    def test_find_affected_bots_returns_engine_type(self):
        """_find_affected_bots 应返回包含 engine_type 的 dict。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import (
            SkillPropagationService,
        )

        mock_repo = MagicMock()
        mock_repo.find_affected_bots_by_skill_uuid.return_value = [
            {"bot_id": "bot-X", "owner_id": "100", "active_engine": "moltis"},
        ]

        svc = SkillPropagationService(
            log_repo=MagicMock(),
            skill_set_repo=mock_repo,
            resolver=MagicMock(), device_sync_dispatcher=MagicMock(),
            skill_set_service_factory=MagicMock(),
        )
        result = svc._find_affected_bots("uuid-X", "dev")

        assert len(result) == 1
        assert result[0]["bot_id"] == "bot-X"
        assert result[0]["owner_id"] == "100"
        assert result[0].get("engine_type") == "moltis"

    def test_find_affected_bots_engine_type_fallback_to_openclaw(self):
        """active_engine 为 None 时应 fallback 到 'openclaw'。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import (
            SkillPropagationService,
        )

        mock_repo = MagicMock()
        mock_repo.find_affected_bots_by_skill_uuid.return_value = [
            {"bot_id": "bot-Y", "owner_id": "200", "active_engine": None},
        ]

        svc = SkillPropagationService(
            log_repo=MagicMock(),
            skill_set_repo=mock_repo,
            resolver=MagicMock(), device_sync_dispatcher=MagicMock(),
            skill_set_service_factory=MagicMock(),
        )
        result = svc._find_affected_bots("skill-999", "prod")

        assert len(result) == 1
        assert result[0].get("engine_type") == "openclaw"

    def test_find_affected_bots_inactive_set_is_skipped(self):
        """is_active=False 的 SkillSet 不应出现在结果中。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import (
            SkillPropagationService,
        )

        mock_repo = MagicMock()
        mock_repo.find_affected_bots_by_skill_uuid.return_value = []

        svc = SkillPropagationService(
            log_repo=MagicMock(),
            skill_set_repo=mock_repo,
            resolver=MagicMock(), device_sync_dispatcher=MagicMock(),
            skill_set_service_factory=MagicMock(),
        )
        result = svc._find_affected_bots("skill-active", "dev")

        assert len(result) == 0


class TestNotifyHotReload:
    def test_notify_hot_reload_called_after_refresh(self, monkeypatch):
        """软链刷新成功后应调用 _notify_hot_reload。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import (
            SkillPropagationService,
        )

        mock_log = MagicMock()
        mock_log.find_recent.return_value = None
        mock_log.create.return_value = {"propagation_id": "p5", "id": 5}

        svc = SkillPropagationService(log_repo=mock_log, skill_set_repo=MagicMock(), resolver=MagicMock(), device_sync_dispatcher=MagicMock(), skill_set_service_factory=MagicMock())
        svc._find_affected_bots = MagicMock(return_value=[
            {"bot_id": "bot-A", "owner_id": "u1", "engine_type": "openclaw"},
        ])

        # mock _refresh_bot 返回成功
        svc._refresh_bot = MagicMock(return_value=True)

        notifications = []
        monkeypatch.setattr(
            svc, "_notify_hot_reload",
            lambda bot_id, skill_uuid: notifications.append((bot_id, skill_uuid)),
        )

        svc.propagate_on_upgrade("uuid-r", "dev", "2")
        assert notifications == [("bot-A", "uuid-r")]

    def test_notify_not_called_on_refresh_failure(self, monkeypatch):
        """_refresh_bot 失败时不应触发热重载通知。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import (
            SkillPropagationService,
        )

        mock_log = MagicMock()
        mock_log.find_recent.return_value = None
        mock_log.create.return_value = {"propagation_id": "p6", "id": 6}

        svc = SkillPropagationService(log_repo=mock_log, skill_set_repo=MagicMock(), resolver=MagicMock(), device_sync_dispatcher=MagicMock(), skill_set_service_factory=MagicMock())
        svc._find_affected_bots = MagicMock(return_value=[
            {"bot_id": "bot-B", "owner_id": "u2", "engine_type": "openclaw"},
        ])
        svc._refresh_bot = MagicMock(return_value=False)  # 失败

        notifications = []
        monkeypatch.setattr(
            svc, "_notify_hot_reload",
            lambda bot_id, skill_uuid: notifications.append((bot_id, skill_uuid)),
        )

        svc.propagate_on_upgrade("uuid-f", "dev", "2")
        assert notifications == []  # 失败不通知


class TestSyncServiceIntegration:
    def test_propagate_on_upgrade_calls_sync_service(self):
        """upgrade 时应触发 sync_service.force_sync。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import (
            SkillPropagationService,
        )
        mock_log = MagicMock()
        mock_log.find_recent.return_value = None
        mock_log.create.return_value = {"propagation_id": "p1", "id": 1}

        mock_sync = MagicMock()
        mock_sync.force_sync.return_value = True

        svc = SkillPropagationService(log_repo=mock_log, sync_service=mock_sync, skill_set_repo=MagicMock(), resolver=MagicMock(), device_sync_dispatcher=MagicMock(), skill_set_service_factory=MagicMock())
        svc._find_affected_bots = MagicMock(return_value=[])

        result = svc.propagate_on_upgrade("uuid-x", "dev", "2")
        mock_sync.force_sync.assert_called_once_with(skill_uuid="uuid-x", env="dev")
        assert result.propagation_log_id is not None

    def test_propagate_on_removal_does_not_force_sync(self):
        """删除场景不立即拉 NAS（让 GC 处理）。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import (
            SkillPropagationService,
        )
        mock_log = MagicMock()
        mock_log.find_recent.return_value = None
        mock_log.create.return_value = {"propagation_id": "p2", "id": 2}

        mock_sync = MagicMock()
        svc = SkillPropagationService(log_repo=mock_log, sync_service=mock_sync, skill_set_repo=MagicMock(), resolver=MagicMock(), device_sync_dispatcher=MagicMock(), skill_set_service_factory=MagicMock())
        svc._find_affected_bots = MagicMock(return_value=[])

        svc.propagate_on_removal("uuid-x", "dev")
        mock_sync.force_sync.assert_not_called()

    def test_sync_service_none_is_safe(self):
        """sync_service 为 None 时不应抛异常。"""
        from agentclaw.community.core.skill_center.services.skill_propagation_service import (
            SkillPropagationService,
        )
        mock_log = MagicMock()
        mock_log.find_recent.return_value = None
        mock_log.create.return_value = {"propagation_id": "p3", "id": 3}

        svc = SkillPropagationService(log_repo=mock_log, sync_service=None, skill_set_repo=MagicMock(), resolver=MagicMock(), device_sync_dispatcher=MagicMock(), skill_set_service_factory=MagicMock())
        svc._find_affected_bots = MagicMock(return_value=[])

        result = svc.propagate_on_upgrade("uuid-y", "dev", "2")
        assert result is not None  # 不抛异常
