"""Unit tests for the bot restart idempotency guard.

Covers BotService.restart_bot's per-bot lock behavior:
- first restart acquires the lock and proceeds (exactly one allocation),
- a concurrent duplicate is suppressed (no second stop/allocation),
- the lock is released when allocation completes, so a later restart is accepted,
- the async allocation releases the lock on every path (success / desktop
  early-return / failure),
- the stale-lock TTL reaper,
- per-bot scoping,
- a synchronous failure before the thread spawns does not orphan the lock.

The guard row is modeled by ``FakeRestartLockRepo`` — an in-memory dict that
honors the UNIQUE(env, entity_id, bot_id) constraint that the real repository
enforces in the DB.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    RESTART_LOCK_TTL_SECONDS,
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotService,
    BotServiceError,
)
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
    BAAS_RESTART_PUBLISH_POLL_TASK,
)
from agentclaw.community.core.devices.services.baas_template_resolver import (
    BaasTemplateResolution,
)


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class FakeRestartLockRepo:
    """In-memory restart-lock repo honoring UNIQUE(env, entity_id, bot_id).

    ``now`` is an injectable clock so the stale-TTL path is testable without
    real time. ``acquire`` mimics the DB: a second insert on a held key fails
    (returns None).
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], SimpleNamespace] = {}
        self.now = datetime(2026, 5, 28, 12, 0, 0)
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self, env, entity_id, bot_id, holder_user_id):
        self.acquire_calls += 1
        key = (env, entity_id, bot_id)
        if key in self.rows:
            return None
        rec = SimpleNamespace(
            id=len(self.rows) + 1,
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            holder_user_id=holder_user_id,
            lock_token=uuid.uuid4().hex,
            gmt_create=self.now,
        )
        self.rows[key] = rec
        return rec

    def get(self, env, entity_id, bot_id):
        return self.rows.get((env, entity_id, bot_id))

    def get_if_stale(self, env, entity_id, bot_id, ttl_seconds):
        rec = self.rows.get((env, entity_id, bot_id))
        if rec is None:
            return None
        if (self.now - rec.gmt_create).total_seconds() >= ttl_seconds:
            return rec
        return None

    def release(self, env, entity_id, bot_id, lock_token):
        self.release_calls += 1
        key = (env, entity_id, bot_id)
        rec = self.rows.get(key)
        # Compare-and-delete: only remove the row if the token still matches.
        if rec is not None and rec.lock_token == lock_token:
            del self.rows[key]
            return True
        return False


class _SyncThread:
    """Drop-in for threading.Thread that runs the target inline on start()."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


def _make_bot(
    bot_id: str = "bot001",
    owner_id: str = "user001",
    status: str = "ACTIVE",
    entity_id: str = "staff_user001",
    bot_type: str = "personal",
    binding_id: int | None = None,
    active_engine: str = "moltis",
    template_type: str = "normalCC",
) -> dict:
    return {
        "bot_id": bot_id,
        "owner_id": owner_id,
        "status": status,
        "entity_id": entity_id,
        "entity_type": "staff",
        "bot_type": bot_type,
        "engine_types": ["moltis", "openclaw"],
        "active_engine": active_engine,
        "template_type": template_type,
        "bot_name": "TestBot",
        "binding_id": binding_id,
    }


def _make_service(
    lock_repo: FakeRestartLockRepo | None = None,
    *,
    bot_repository: MagicMock | None = None,
    device_binding_repo: MagicMock | None = None,
    device_provider: MagicMock | None = None,
    baas_service_provider=None,
    bot_publish_repo: MagicMock | None = None,
    baas_template_resolver: MagicMock | None = None,
    task_queue_service: MagicMock | None = None,
) -> BotService:
    if lock_repo is None:
        lock_repo = FakeRestartLockRepo()
    svc = BotService.__new__(BotService)
    svc._repository = bot_repository if bot_repository is not None else MagicMock()
    svc._restart_lock_repo = lock_repo
    svc._bot_publish_provider = lambda: MagicMock()
    svc._device_service_provider = (lambda: device_provider) if device_provider is not None else (lambda: MagicMock())
    svc._oss_record_repo = MagicMock()
    svc._skill_set_factory = MagicMock()
    svc._template_service = MagicMock()
    svc._baas_service_provider = baas_service_provider
    svc._device_binding_repo = device_binding_repo if device_binding_repo is not None else MagicMock()
    svc._bot_publish_repo = bot_publish_repo if bot_publish_repo is not None else MagicMock()
    svc._baas_template_resolver = baas_template_resolver
    svc._task_queue_service = task_queue_service
    svc._drm_reader = MagicMock()
    svc._drm_reader.read.return_value = None
    svc._bcn_service = MagicMock()
    return svc


# ===========================================================================
# restart_bot orchestration
# ===========================================================================


class TestRestartGuardOrchestration:
    @pytest.mark.parametrize("status", ["REACTIVATING", "PENDING"])
    def test_restart_during_activation_is_idempotent(self, status):
        """Activation owns the lifecycle while the bot is starting."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status=status)
        svc._repository.get_by_id_and_owner.return_value = bot

        with patch.object(svc, "stop_bot") as stop, \
             patch.object(svc, "start_bot") as start:
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        assert result["status"] == status
        assert result["restart_in_progress"] is True
        assert result["message"] == "Bot activation is in progress"
        assert repo.acquire_calls == 0
        stop.assert_not_called()
        start.assert_not_called()

    def test_recycled_bot_restart_is_rejected_without_side_effects(self):
        """RECYCLED bots must only return through the explicit activate flow."""
        repo = FakeRestartLockRepo()
        device_service = MagicMock()
        svc = _make_service(repo, device_provider=device_service)
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            status="RECYCLED",
            binding_id=42,
        )

        with patch.object(svc, "stop_bot") as stop, \
             patch.object(svc, "start_bot") as start:
            with pytest.raises(BotInvalidLifecycleStateError) as exc_info:
                svc.restart_bot(bot_id="bot001", user_id="user001")

        assert exc_info.value.current_status == "RECYCLED"
        assert repo.acquire_calls == 0
        device_service.get_device.assert_not_called()
        stop.assert_not_called()
        start.assert_not_called()

    def test_pending_binding_restart_is_idempotent(self):
        """A newly-created binding may not be ready for BaaS update yet."""
        repo = FakeRestartLockRepo()
        device_service = MagicMock()
        device_service.get_device.return_value = {
            "id": 42,
            "device_provider": "baas",
            "status": DeviceBindingStatus.PENDING,
        }
        baas = MagicMock()
        svc = _make_service(
            repo,
            device_provider=device_service,
            baas_service_provider=lambda: baas,
        )
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            status="ACTIVE",
            binding_id=42,
        )

        with patch.object(svc, "stop_bot") as stop, \
             patch.object(svc, "start_bot") as start:
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        assert result["restart_in_progress"] is True
        assert result["message"] == "Bot activation is in progress"
        assert repo.acquire_calls == 0
        baas.upgrade_bot.assert_not_called()
        stop.assert_not_called()
        start.assert_not_called()

    @pytest.mark.parametrize("bot_status", ["ACTIVE", "FAILED"])
    def test_failed_arca_binding_restart_recovers_in_place(self, bot_status):
        """Arca failures are recoverable through the existing stop/start path."""
        repo = FakeRestartLockRepo()
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42,
            device_provider="arca",
            status=DeviceBindingStatus.FAILED,
        )
        svc = _make_service(repo, device_provider=device_service)
        bot = _make_bot(
            status=bot_status,
            binding_id=42,
        )
        svc._repository.get_by_id_and_owner.return_value = bot

        with patch.object(svc, "stop_bot", return_value=True) as stop, \
             patch.object(svc, "start_bot", return_value=bot) as start:
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        assert result == bot
        stop.assert_called_once()
        start.assert_called_once()
        assert start.call_args.kwargs["device_provider"] == "arca"

    def test_failed_bot_with_failed_baas_binding_restarts_in_place(self):
        """A failed BaaS bot keeps its binding and uses the BaaS update path."""
        repo = FakeRestartLockRepo()
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42,
            device_provider="baas",
            device_id="BOT-uuid-9",
            status=DeviceBindingStatus.FAILED,
        )
        svc = _make_service(
            repo,
            device_provider=device_service,
            baas_service_provider=lambda: MagicMock(),
        )
        bot = _make_bot(status="FAILED", binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        with patch.object(svc, "_restart_bot_baas", return_value=bot) as restart_baas, \
             patch.object(svc, "stop_bot") as stop, \
             patch.object(svc, "start_bot") as start:
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        assert result == bot
        restart_baas.assert_called_once_with(
            bot_id="bot001",
            user_id="user001",
            binding_id=42,
            bot=bot,
        )
        stop.assert_not_called()
        start.assert_not_called()

    def test_failed_bot_without_binding_is_rejected(self):
        """A failed bot without provider history must not re-enter create rollout."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            status="FAILED",
            binding_id=None,
        )

        with patch.object(svc, "stop_bot") as stop, \
             patch.object(svc, "start_bot") as start:
            with pytest.raises(BotInvalidLifecycleStateError) as exc_info:
                svc.restart_bot(bot_id="bot001", user_id="user001")

        assert exc_info.value.current_status == "FAILED_WITHOUT_BINDING"
        assert repo.acquire_calls == 0
        stop.assert_not_called()
        start.assert_not_called()

    def test_released_binding_restart_is_rejected(self):
        """A released binding no longer identifies a live runtime to restart."""
        repo = FakeRestartLockRepo()
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42,
            device_provider="arca",
            status=DeviceBindingStatus.RELEASED,
        )
        svc = _make_service(repo, device_provider=device_service)
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            status="FAILED",
            binding_id=42,
        )

        with patch.object(svc, "stop_bot") as stop, \
             patch.object(svc, "start_bot") as start:
            with pytest.raises(BotInvalidLifecycleStateError) as exc_info:
                svc.restart_bot(bot_id="bot001", user_id="user001")

        assert exc_info.value.current_status == "BINDING_RELEASED"
        assert repo.acquire_calls == 0
        stop.assert_not_called()
        start.assert_not_called()

    def test_first_restart_acquires_and_runs_once(self):
        """First restart acquires the lock and triggers exactly one allocation."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="ACTIVE")
        svc._repository.get_by_id_and_owner.return_value = bot

        with patch.object(svc, "stop_bot", return_value=True) as stop, \
             patch.object(svc, "start_bot", return_value=bot) as start:
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        assert stop.call_count == 1
        assert start.call_count == 1
        # Lock identity was handed to start_bot, built from the bot's own
        # entity_id/bot_id and the current env (not a hardcoded/shared key),
        # plus the fencing token from the acquired row.
        from agentclaw.community.utils.env_utils import get_current_env
        _, kwargs = start.call_args
        env, entity_id, bot_id, token = kwargs["restart_lock_key"]
        assert (env, entity_id, bot_id) == (get_current_env(), "staff_user001", "bot001")
        held = repo.get(env, entity_id, bot_id)
        assert token == held.lock_token  # the key carries the row's real token
        # One lock row currently held (allocation "in progress" — start_bot is
        # mocked so it never releases).
        assert len(repo.rows) == 1
        assert result == bot

    def test_restart_passes_current_provider_to_start(self):
        """Restart must preserve the provider from the current binding; creation
        rollout must not re-decide an existing bot's lifecycle provider."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="ACTIVE", binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42,
            device_provider="arca",
        )
        svc._device_service_provider = lambda: device_service

        with patch.object(svc, "stop_bot", return_value=True) as stop, \
             patch.object(svc, "start_bot", return_value=bot) as start:
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        assert result == bot
        stop.assert_called_once()
        start.assert_called_once()
        assert start.call_args.kwargs["device_provider"] == "arca"

    def test_restart_logs_preserved_provider_before_stop(self):
        """Regression evidence: pre-release provider preservation must be visible
        in logs so staging rollback checks do not rely on DB side evidence only."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="ACTIVE", binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42,
            device_provider="arca",
        )
        svc._device_service_provider = lambda: device_service

        with patch.object(svc, "stop_bot", return_value=True), \
             patch.object(svc, "start_bot", return_value=bot), \
             patch("agentclaw.community.core.bot_management.services.bot_service.logger.info") as log_info:
            svc.restart_bot(bot_id="bot001", user_id="user001")

        messages = [
            str(call.args[0])
            for call in log_info.call_args_list
            if call.args
        ]
        assert any(
            "[bot_service.restart_bot] preserve device_provider" in msg
            and "bot_id=bot001" in msg
            and "binding_id=42" in msg
            and "device_provider=arca" in msg
            for msg in messages
        )

    def test_restart_baas_personal_upgrades_bot_with_no_migration(self):
        """personal baas bot 重启走 BaaSService.upgrade_bot（走 /update），mig=None，
        bot_uuid 取 binding.device_id 不变，不 stop+start，binding 不变。"""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="ACTIVE", binding_id=42, bot_type="personal")
        svc._repository.get_by_id_and_owner.return_value = bot

        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42, device_provider="baas", device_id="BOT-uuid-9",
        )
        svc._device_service_provider = lambda: device_service
        baas = MagicMock()
        svc._baas_service_provider = lambda: baas

        with patch.object(svc, "stop_bot") as stop, patch.object(svc, "start_bot") as start:
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        baas.restart_bot.assert_not_called()
        baas.upgrade_bot.assert_called_once()
        kwargs = baas.upgrade_bot.call_args.kwargs
        assert kwargs["bot_uuid"] == "BOT-uuid-9"  # 取自 binding.device_id 不变
        assert kwargs["bot"] == bot
        assert kwargs["owner_id"] == "user001"
        assert kwargs["migration_path"] is None  # personal 不迁移
        assert "stage" not in kwargs
        stop.assert_not_called()
        start.assert_not_called()
        assert result == bot

    def test_restart_baas_service_upgrades_with_no_migration_path(self):
        """service 草稿重启不传迁移源，避免把发布态 build 目录带入普通 restart。"""
        repo = FakeRestartLockRepo()
        publish_repo = MagicMock()
        publish_repo.get_by_publish_bot_id.side_effect = AssertionError(
            "draft restart must not query publish migration_path"
        )
        svc = _make_service(repo, bot_publish_repo=publish_repo)
        bot = _make_bot(status="ACTIVE", binding_id=42, bot_type="service")
        svc._repository.get_by_id_and_owner.return_value = bot
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42, device_provider="baas", device_id="BOT-uuid-9",
        )
        svc._device_service_provider = lambda: device_service
        baas = MagicMock()
        svc._baas_service_provider = lambda: baas

        with patch.object(svc, "stop_bot") as stop, patch.object(svc, "start_bot") as start:
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        baas.upgrade_bot.assert_called_once()
        kwargs = baas.upgrade_bot.call_args.kwargs
        assert kwargs["bot_uuid"] == "BOT-uuid-9"
        assert kwargs["migration_path"] is None
        assert kwargs["stage"] == "draft"
        publish_repo.get_by_publish_bot_id.assert_not_called()
        stop.assert_not_called()
        start.assert_not_called()
        assert result == bot

    def test_restart_baas_service_ignores_existing_publish_migration_path(self):
        """普通 restart 只重启当前 bot；历史发布记录里的 migration_path 不参与。"""
        repo = FakeRestartLockRepo()
        publish_repo = MagicMock()
        publish_repo.get_by_publish_bot_id.return_value = SimpleNamespace(
            ext={"migration_path": "/opt/nfs/bot-data/3/openclaw"}
        )
        svc = _make_service(repo, bot_publish_repo=publish_repo)
        bot = _make_bot(status="ACTIVE", binding_id=42, bot_type="service")
        svc._repository.get_by_id_and_owner.return_value = bot
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42, device_provider="baas", device_id="BOT-uuid-9",
        )
        svc._device_service_provider = lambda: device_service
        baas = MagicMock()
        svc._baas_service_provider = lambda: baas

        with patch.object(svc, "stop_bot"), patch.object(svc, "start_bot"):
            svc.restart_bot(bot_id="bot001", user_id="user001")

        kwargs = baas.upgrade_bot.call_args.kwargs
        assert kwargs["migration_path"] is None
        assert kwargs["stage"] == "draft"
        publish_repo.get_by_publish_bot_id.assert_not_called()

    def test_restart_baas_uses_resolved_template_uuid_for_upgrade(self):
        """BaaS 原地重启也要按当前 bot 上下文解析 template_uuid。

        update/create 走的是同一个 BaaS create payload；如果 restart 不显式传
        template_uuid，BaasService 会落回默认 OpenClaw 模板，Claude/Hermes 等
        引擎重启时就会用错模板。
        """
        from agentclaw.community.utils.env_utils import get_current_env

        repo = FakeRestartLockRepo()
        publish_repo = MagicMock()
        resolver = MagicMock()
        resolver.resolve_template.return_value = BaasTemplateResolution(
            template_uid="claude_code_bot_template",
            template_uuid="TEMPLATE-claude-code",
            source="system_config",
        )
        template_config = {"image": "reg.example/custom:latest"}
        svc = _make_service(
            repo,
            bot_publish_repo=publish_repo,
            baas_template_resolver=resolver,
        )
        svc._template_service.get_template_config.return_value = template_config
        bot = _make_bot(
            status="ACTIVE",
            binding_id=42,
            bot_type="service",
            active_engine="claude_code",
            template_type="normalCC",
        )
        svc._repository.get_by_id_and_owner.return_value = bot
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42, device_provider="baas", device_id="BOT-uuid-9",
        )
        svc._device_service_provider = lambda: device_service
        baas = MagicMock()
        svc._baas_service_provider = lambda: baas

        with patch.object(svc, "stop_bot"), patch.object(svc, "start_bot"):
            svc.restart_bot(bot_id="bot001", user_id="user001")

        resolver.resolve_template.assert_called_once_with(
            bot_id="bot001",
            user_id="user001",
            env=get_current_env(),
            bot_type="service",
            engine_type="claude_code",
            template_type="normalCC",
            template_config=template_config,
        )
        resolver.resolve_template_uid.assert_not_called()
        resolver.resolve_template_uuid.assert_not_called()
        assert baas.upgrade_bot.call_args.kwargs["template_uuid"] == "TEMPLATE-claude-code"
        assert baas.upgrade_bot.call_args.kwargs["migration_path"] is None
        publish_repo.get_by_publish_bot_id.assert_not_called()

    def test_restart_baas_service_does_not_require_published_version(self):
        """service 草稿重启不依赖发布记录；无 publish_record 也应提交 BaaS update。"""
        repo = FakeRestartLockRepo()
        publish_repo = MagicMock()
        publish_repo.get_by_publish_bot_id.return_value = None
        svc = _make_service(repo, bot_publish_repo=publish_repo)
        bot = _make_bot(status="ACTIVE", binding_id=42, bot_type="service")
        svc._repository.get_by_id_and_owner.return_value = bot
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42, device_provider="baas", device_id="BOT-uuid-9",
        )
        svc._device_service_provider = lambda: device_service
        baas = MagicMock()
        svc._baas_service_provider = lambda: baas

        with patch.object(svc, "stop_bot"), patch.object(svc, "start_bot"):
            svc.restart_bot(bot_id="bot001", user_id="user001")
        baas.upgrade_bot.assert_called_once()
        assert baas.upgrade_bot.call_args.kwargs["migration_path"] is None
        publish_repo.get_by_publish_bot_id.assert_not_called()

    def test_restart_baas_aicoding_personal_coding_registers_bcn_before_upgrade(self):
        """BaaS 原地重启不经过 start_bot，也必须补齐 AI Coding 的 BCN 注册。"""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._drm_reader.read.return_value = "true"
        svc._template_service.get_template_config.return_value = {}
        bot = _make_bot(
            status="ACTIVE",
            binding_id=42,
            bot_type="personal",
            active_engine="aicoding",
            template_type="personalCoding",
        )
        svc._repository.get_by_id_and_owner.return_value = bot
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42, device_provider="baas", device_id="BOT-uuid-9",
        )
        svc._device_service_provider = lambda: device_service
        call_order: list[str] = []
        svc._bcn_service.register_provider_bot.side_effect = lambda **_: (
            call_order.append("bcn")
            or {
                "bot_uuid": "bcn-bot-1",
                "bot_runtime_token": "token",
            }
        )
        baas = MagicMock()
        baas.upgrade_bot.side_effect = lambda **_: call_order.append("upgrade") or {}
        svc._baas_service_provider = lambda: baas

        svc.restart_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot001",
            owner_workno="user001",
            name="TestBot",
            summary="",
        )
        baas.upgrade_bot.assert_called_once()
        assert call_order == ["bcn", "upgrade"]

    def test_restart_baas_ineligible_template_does_not_register_bcn(self):
        """applicationCoding 不在 BCN Provider 注册范围内。"""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._drm_reader.read.return_value = "true"
        svc._template_service.get_template_config.return_value = {}
        bot = _make_bot(
            status="ACTIVE",
            binding_id=42,
            bot_type="personal",
            active_engine="aicoding",
            template_type="applicationCoding",
        )
        svc._repository.get_by_id_and_owner.return_value = bot
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42, device_provider="baas", device_id="BOT-uuid-9",
        )
        svc._device_service_provider = lambda: device_service
        baas = MagicMock()
        svc._baas_service_provider = lambda: baas

        svc.restart_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_not_called()
        baas.upgrade_bot.assert_called_once()

    def test_restart_baas_bcn_registration_failure_does_not_block_upgrade(self):
        """BCN 注册失败沿用既有 best-effort 语义，不阻塞 BaaS 重启。"""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._drm_reader.read.return_value = "true"
        svc._template_service.get_template_config.return_value = {}
        svc._bcn_service.register_provider_bot.side_effect = RuntimeError("bcn down")
        bot = _make_bot(
            status="ACTIVE",
            binding_id=42,
            bot_type="personal",
            active_engine="aicoding",
            template_type="personalCoding",
        )
        svc._repository.get_by_id_and_owner.return_value = bot
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42, device_provider="baas", device_id="BOT-uuid-9",
        )
        svc._device_service_provider = lambda: device_service
        baas = MagicMock()
        svc._baas_service_provider = lambda: baas

        svc.restart_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_called_once()
        baas.upgrade_bot.assert_called_once()

    def test_restart_arca_still_uses_stop_start(self):
        """arca bot 仍走 stop+start，不进 baas 原地分支。"""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="ACTIVE", binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(id=42, device_provider="arca")
        svc._device_service_provider = lambda: device_service
        svc._baas_service_provider = lambda: MagicMock()

        with patch.object(svc, "stop_bot", return_value=True) as stop, \
             patch.object(svc, "start_bot", return_value=bot) as start:
            svc.restart_bot(bot_id="bot001", user_id="user001")
        stop.assert_called_once()
        start.assert_called_once()

    def test_restart_aborts_when_current_binding_provider_is_missing(self):
        """A bot with a binding but no provider fact is inconsistent. Restart
        must fail closed instead of entering create rollout."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="ACTIVE", binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42,
            device_provider="",
        )
        svc._device_service_provider = lambda: device_service

        with pytest.raises(BotServiceError, match="missing device_provider"):
            svc.restart_bot(bot_id="bot001", user_id="user001")

        assert repo.acquire_calls == 0

    def test_start_bot_hands_device_provider_to_allocator(self):
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="PENDING")
        svc._repository.get_by_id_and_owner.return_value = bot

        with patch.object(svc, "_register_bot_to_bcn_as_provider"), \
             patch.object(svc, "_allocate_device_async") as allocate:
            svc.start_bot(
                bot_id="bot001",
                user_id="user001",
                device_provider="baas",
            )

        allocate.assert_called_once()
        assert allocate.call_args.kwargs["device_provider"] == "baas"

    def test_start_bot_logs_explicit_provider_context(self):
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="PENDING", bot_type="personal")
        bot["active_engine"] = "openclaw"
        svc._repository.get_by_id_and_owner.return_value = bot

        with patch.object(svc, "_register_bot_to_bcn_as_provider"), \
             patch.object(svc, "_allocate_device_async"), \
             patch("agentclaw.community.core.bot_management.services.bot_service.logger.info") as log_info:
            svc.start_bot(
                bot_id="bot001",
                user_id="user001",
                device_provider="baas",
            )

        messages = [
            str(call.args[0])
            for call in log_info.call_args_list
            if call.args
        ]
        assert any(
            "[bot_service.start_bot] start requested" in msg
            and "bot_id=bot001" in msg
            and "active_engine=openclaw" in msg
            and "bot_type=personal" in msg
            and "explicit_device_provider=baas" in msg
            for msg in messages
        )

    def test_concurrent_duplicate_is_suppressed(self):
        """A second restart while one is in progress is suppressed (no rework)."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="ACTIVE")
        svc._repository.get_by_id_and_owner.return_value = bot

        # start_bot is mocked and never releases → lock stays held after #1.
        with patch.object(svc, "stop_bot", return_value=True) as stop, \
             patch.object(svc, "start_bot", return_value=bot) as start:
            svc.restart_bot(bot_id="bot001", user_id="user001")
            result2 = svc.restart_bot(bot_id="bot001", user_id="user001")

        # stop/start ran only for the first request.
        assert stop.call_count == 1
        assert start.call_count == 1
        # Both requests attempted to acquire (proving suppression came from the
        # lock mechanism). The first request acquires once; the suppressed second
        # makes two attempts (the authoritative re-check that disambiguates
        # "still held" from "released since"), so 1 + 2 == 3.
        assert repo.acquire_calls == 3
        # Duplicate returns the in-progress shape (PENDING, binding cleared) so
        # the client polls correctly — without persisting that reset.
        assert result2["status"] == "PENDING"
        assert result2["binding_id"] is None
        assert result2["device_id"] is None
        # No DB write happened in the suppression path (response shaped only).
        svc._repository.update_by_owner.assert_not_called()
        assert len(repo.rows) == 1

    def test_suppressed_returns_pending_even_when_db_still_active(self):
        """The race fix: a suppressed duplicate reports PENDING even if the DB
        still reads ACTIVE because the in-flight restart's stop_bot hasn't
        flipped the row yet. Response is shaped, not persisted."""
        from agentclaw.community.utils.env_utils import get_current_env

        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="ACTIVE")
        bot["binding_id"] = 42
        bot["device_id"] = "dev-1"
        svc._repository.get_by_id_and_owner.return_value = bot
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            id=42,
            device_provider="arca",
            status=DeviceBindingStatus.ACTIVE,
        )
        svc._device_service_provider = lambda: device_service
        # Another request already holds the lock; its stop_bot hasn't run, so
        # the DB still reads ACTIVE with a live binding.
        repo.acquire(get_current_env(), "staff_user001", "bot001", "other_user")

        result = svc.restart_bot(bot_id="bot001", user_id="user001")

        assert result["status"] == "PENDING"
        assert result["binding_id"] is None
        assert result["device_id"] is None
        # Shaped response only — no DB write in the suppression path.
        svc._repository.update_by_owner.assert_not_called()

    def test_reaccepted_after_completion(self):
        """Once allocation completes (lock released), a later restart is accepted."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot(status="ACTIVE")
        svc._repository.get_by_id_and_owner.return_value = bot

        # start_bot simulates completion: release the lock it was handed.
        def _start(**kwargs):
            key = kwargs.get("restart_lock_key")
            if key:
                repo.release(*key)
            return bot

        with patch.object(svc, "stop_bot", return_value=True), \
             patch.object(svc, "start_bot", side_effect=_start) as start:
            svc.restart_bot(bot_id="bot001", user_id="user001")
            assert len(repo.rows) == 0  # released
            svc.restart_bot(bot_id="bot001", user_id="user001")

        assert start.call_count == 2  # both accepted

    def test_per_bot_scoping(self):
        """A restart on bot A does not block a concurrent restart on bot B."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)

        a = svc._try_acquire_restart_lock("dev", "ent", "botA", "u1")
        b = svc._try_acquire_restart_lock("dev", "ent", "botB", "u1")

        assert a is not None and b is not None
        assert len(repo.rows) == 2

    def test_sync_failure_before_spawn_releases_lock(self):
        """If stop_bot raises before the thread spawns, the lock is not orphaned."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        bot = _make_bot()
        svc._repository.get_by_id_and_owner.return_value = bot

        with patch.object(svc, "stop_bot", side_effect=RuntimeError("boom")):
            with pytest.raises(BotServiceError):
                svc.restart_bot(bot_id="bot001", user_id="user001")

        # Lock was acquired (before stop_bot) then released by restart_bot's
        # finally — pins the acquire-before-stop ordering, not just an empty table.
        assert repo.acquire_calls == 1
        assert repo.release_calls == 1
        assert len(repo.rows) == 0

    def test_missing_entity_id_rejected(self):
        """A bot without entity_id is rejected, never poisoning the lock key."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._repository.get_by_id_and_owner.return_value = _make_bot(entity_id="")

        with pytest.raises(BotServiceError, match="no entity_id"):
            svc.restart_bot(bot_id="bot001", user_id="user001")
        assert len(repo.rows) == 0

    def test_bot_not_found_raises_before_acquire(self):
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._repository.get_by_id_and_owner.return_value = None

        with pytest.raises(BotNotFoundError):
            svc.restart_bot(bot_id="bot001", user_id="user001")
        assert repo.acquire_calls == 0


# ===========================================================================
# _try_acquire_restart_lock — stale reaper
# ===========================================================================


class TestStaleReaper:
    def test_fresh_lock_blocks(self):
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        first = svc._try_acquire_restart_lock("dev", "ent", "bot001", "u1")
        second = svc._try_acquire_restart_lock("dev", "ent", "bot001", "u1")
        assert first is not None
        assert second is None  # held & fresh → suppressed

    def test_stale_lock_reaped_and_reacquired(self):
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._try_acquire_restart_lock("dev", "ent", "bot001", "u1")
        # Advance the clock past the TTL → the held row is now stale.
        repo.now += timedelta(seconds=RESTART_LOCK_TTL_SECONDS + 1)
        reacquired = svc._try_acquire_restart_lock("dev", "ent", "bot001", "u2")
        assert reacquired is not None
        assert reacquired.holder_user_id == "u2"
        assert len(repo.rows) == 1

    def test_released_between_acquire_and_check_is_reacquired(self):
        """Race: the holder releases the row between our first acquire (conflict)
        and the staleness check. get_if_stale sees no row, but the second acquire
        must still pick the lock up — a legitimate restart is NOT dropped."""
        repo = MagicMock()
        # 1st acquire conflicts (held); the row then vanishes; 2nd acquire wins.
        won = SimpleNamespace(lock_token="tok")
        repo.acquire.side_effect = [None, won]
        repo.get_if_stale.return_value = None  # row already gone at check time
        svc = _make_service(repo)

        result = svc._try_acquire_restart_lock("dev", "ent", "bot001", "u1")

        assert result is won  # proceeded, not suppressed
        assert repo.acquire.call_count == 2
        repo.release.assert_not_called()  # nothing to reap

    def test_fresh_held_suppressed_after_second_attempt(self):
        """If the row is genuinely still held & fresh, both acquire attempts fail
        and we suppress (return None)."""
        repo = MagicMock()
        repo.acquire.side_effect = [None, None]  # held on both attempts
        repo.get_if_stale.return_value = None  # fresh, not stale
        svc = _make_service(repo)

        result = svc._try_acquire_restart_lock("dev", "ent", "bot001", "u1")

        assert result is None  # suppressed
        assert repo.acquire.call_count == 2
        repo.release.assert_not_called()

    def test_reaper_does_not_steal_lock_reacquired_by_another_worker(self):
        """Fencing race in the reaper path: we read a stale row (token "A"), but
        before our reap-release fires another worker already reaped + reacquired,
        so the row now holds a *different* token. Our release must compare-and-
        delete on "A" (a no-op here), NOT blindly delete the key — otherwise we'd
        steal the new holder's fresh lock. The follow-up acquire then fails and we
        correctly suppress this duplicate."""
        repo = MagicMock()
        # 1st acquire conflicts; after our no-op release, the 2nd acquire still
        # fails because the *other* worker holds the freshly-reacquired lock.
        repo.acquire.side_effect = [None, None]
        stale = SimpleNamespace(
            lock_token="A", gmt_create=datetime(2026, 5, 28, 12, 0, 0)
        )
        repo.get_if_stale.return_value = stale
        repo.release.return_value = False  # token "A" no longer matches → no-op
        svc = _make_service(repo)

        result = svc._try_acquire_restart_lock("dev", "ent", "bot001", "u1")

        assert result is None  # suppressed — did not steal the newer lock
        # The reap released using the STALE row's token (fencing), not a blind
        # delete of whatever currently occupies the key.
        repo.release.assert_called_once_with("dev", "ent", "bot001", "A")
        assert repo.acquire.call_count == 2


# ===========================================================================
# _allocate_device_async releases the lock on every path
# ===========================================================================


class TestAsyncReleasesLock:
    def test_release_on_desktop_early_return(self):
        """Desktop early-return inside do_allocate still releases the lock."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        # Pre-hold the lock as restart_bot would.
        held = repo.acquire("dev", "ent", "desktop_1", "u1")
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            bot_id="desktop_1", bot_type="desktop"
        )

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _SyncThread,
        ):
            svc._allocate_device_async(
                bot_id="desktop_1",
                user_id="u1",
                nick_name="u1",
                entity_id="ent",
                entity_type="staff",
                engine_types=["moltis"],
                restart_lock_key=("dev", "ent", "desktop_1", held.lock_token),
            )

        assert len(repo.rows) == 0  # released in finally

    def test_release_on_allocation_failure(self):
        """An exception during allocation still releases the lock."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        held = repo.acquire("dev", "ent", "bot001", "u1")
        svc._repository.get_by_id_and_owner.return_value = _make_bot()
        # Make device allocation blow up after the desktop check.
        svc._device_service_provider = MagicMock(side_effect=RuntimeError("alloc failed"))

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _SyncThread,
        ):
            svc._allocate_device_async(
                bot_id="bot001",
                user_id="u1",
                nick_name="u1",
                entity_id="ent",
                entity_type="staff",
                engine_types=["moltis"],
                restart_lock_key=("dev", "ent", "bot001", held.lock_token),
            )

        assert len(repo.rows) == 0  # released in finally despite failure

    def test_allocator_passes_device_provider_to_apply_device(self):
        """The async allocator is the last hop before DeviceServiceRouter; it
        must preserve the explicit provider handed off by restart."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            status="PENDING"
        )

        device_service = MagicMock()
        device_service.apply_device.return_value = SimpleNamespace(
            id=7,
            device_id="device-7",
            device_provider="baas",
            status="PENDING",
        )
        svc._device_service_provider = lambda: device_service
        svc._skill_set_factory.create.return_value.get_symlink_mappings.return_value = []
        svc._template_service.get_template_config.return_value = None
        svc._query_admin_worknos = MagicMock(return_value=[])

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _SyncThread,
        ):
            svc._allocate_device_async(
                bot_id="bot001",
                user_id="user001",
                nick_name="u1",
                entity_id="staff_user001",
                entity_type="staff",
                engine_types=["moltis"],
                device_provider="baas",
            )

        device_service.apply_device.assert_called_once()
        assert device_service.apply_device.call_args.kwargs["device_provider"] == "baas"

    def test_allocator_logs_explicit_provider_before_apply_device(self):
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            status="PENDING",
            bot_type="personal",
        )

        device_service = MagicMock()
        device_service.apply_device.return_value = SimpleNamespace(
            id=7,
            device_id="device-7",
            device_provider="baas",
            status="PENDING",
        )
        svc._device_service_provider = lambda: device_service
        svc._skill_set_factory.create.return_value.get_symlink_mappings.return_value = []
        svc._template_service.get_template_config.return_value = None
        svc._query_admin_worknos = MagicMock(return_value=[])

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _SyncThread,
        ), patch("agentclaw.community.core.bot_management.services.bot_service.logger.info") as log_info:
            svc._allocate_device_async(
                bot_id="bot001",
                user_id="user001",
                nick_name="u1",
                entity_id="staff_user001",
                entity_type="staff",
                engine_types=["openclaw"],
                active_engine="openclaw",
                device_provider="baas",
            )

        messages = [
            str(call.args[0])
            for call in log_info.call_args_list
            if call.args
        ]
        assert any(
            "[bot_service._allocate_device_async] allocation requested" in msg
            and "bot_id=bot001" in msg
            and "active_engine=openclaw" in msg
            and "bot_type=personal" in msg
            and "explicit_device_provider=baas" in msg
            and "force_nas=False" in msg
            for msg in messages
        )

    def test_late_release_does_not_delete_newer_holders_lock(self):
        """Fencing race end-to-end: restart #1's allocation overran the TTL and
        was reaped; a *new* restart (#2) then acquired a fresh lock on the same
        key. When #1's allocation finally finishes and releases with its OLD
        token, the compare-and-delete must be a no-op — #2's lock survives, so a
        legitimately in-progress restart is never torn down by a zombie thread."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)

        # Restart #1 holds the lock (token A) — its allocation thread is the one
        # we'll run below, simulating an overrun.
        held_a = repo.acquire("dev", "ent", "desktop_1", "u1")
        # Meanwhile the reaper deletes the stale row and restart #2 reacquires
        # (token B now occupies the key).
        repo.release("dev", "ent", "desktop_1", held_a.lock_token)
        held_b = repo.acquire("dev", "ent", "desktop_1", "u2")
        assert held_b.lock_token != held_a.lock_token

        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            bot_id="desktop_1", bot_type="desktop"
        )

        # #1's allocation completes late and releases with its STALE token A.
        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _SyncThread,
        ):
            svc._allocate_device_async(
                bot_id="desktop_1",
                user_id="u1",
                nick_name="u1",
                entity_id="ent",
                entity_type="staff",
                engine_types=["moltis"],
                restart_lock_key=("dev", "ent", "desktop_1", held_a.lock_token),
            )

        # #2's fresh lock is untouched — the late release of token A was a no-op.
        survivor = repo.get("dev", "ent", "desktop_1")
        assert survivor is not None
        assert survivor.lock_token == held_b.lock_token

    def test_no_release_when_no_lock_key(self):
        """Non-restart flows (no lock key) never touch the lock repo."""
        repo = FakeRestartLockRepo()
        svc = _make_service(repo)
        svc._repository.get_by_id_and_owner.return_value = _make_bot(bot_type="desktop")

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _SyncThread,
        ):
            svc._allocate_device_async(
                bot_id="bot001",
                user_id="u1",
                nick_name="u1",
                entity_id="ent",
                entity_type="staff",
                engine_types=["moltis"],
                restart_lock_key=None,
            )

        assert repo.release_calls == 0


# ===========================================================================
# _restart_bot_baas — PENDING 置位 + durable queue enqueue
# ===========================================================================


class TestRestartBaasPendingAndQueue:
    def test_personal_baas_restart_marks_pending_and_enqueues_task(self):
        """personal baas 重启 → upgrade_bot 取 publish_id；bot/binding 双置 PENDING；
        持久化 restart poll task。"""
        baas = MagicMock()
        baas.upgrade_bot.return_value = {"publish_id": 9377, "bot_uuid": "BOT-x"}
        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )
        device_provider.get_device.return_value.device_id = "BOT-x"
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot-1", "owner_id": "u1", "binding_id": 42,
            "bot_type": "personal", "entity_id": "u1",
        }
        bind_repo = MagicMock()
        task_queue_service = MagicMock()

        svc = _make_service(
            bot_repository=bot_repo,
            device_binding_repo=bind_repo,
            device_provider=device_provider,
            baas_service_provider=lambda: baas,
            task_queue_service=task_queue_service,
        )
        svc._restart_bot_baas(
            bot_id="bot-1", user_id="u1", binding_id=42, bot=bot_repo.get_by_id_and_owner.return_value,
        )

        # upgrade_bot 调用
        baas.upgrade_bot.assert_called_once()
        assert baas.upgrade_bot.call_args.kwargs["mount_home_dir_storage"] is True
        # bot 行置 PENDING，并记录当前 restart publish 轮次。
        bot_repo.update_by_owner.assert_called_with(
            "bot-1",
            "u1",
            {"status": "PENDING", "ext": {"restart_publish_id": "9377"}},
        )
        # binding 置 PENDING
        bind_repo.update_status.assert_called_with(
            binding_id=42, status=DeviceBindingStatus.PENDING
        )
        bind_repo.update_device_props.assert_called_once_with(
            binding_id=42,
            props={
                "publish_id": "9377",
                "restart_publish_id": "9377",
                "restart_request_id": baas.upgrade_bot.call_args.kwargs["request_id"],
            },
        )
        task_queue_service.enqueue.assert_called_once()
        enqueue_args = task_queue_service.enqueue.call_args
        assert enqueue_args.args[0] == BAAS_RESTART_PUBLISH_POLL_TASK
        assert enqueue_args.args[1]["binding_id"] == 42
        assert enqueue_args.args[1]["bot_id"] == "bot-1"
        assert enqueue_args.args[1]["owner_id"] == "u1"
        assert enqueue_args.args[1]["publish_id"] == 9377
        assert enqueue_args.args[1]["bot_uuid"] == "BOT-x"
        assert enqueue_args.kwargs == {"deadline_seconds": 86400}

    def test_baas_restart_missing_publish_id_skips_poll_task(self):
        """publish_id 缺失：仅 log，不抛，不入队；status 仍置 PENDING。"""
        baas = MagicMock()
        baas.upgrade_bot.return_value = {"bot_uuid": "BOT-x"}  # 无 publish_id
        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE, device_id="BOT-x",
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot-1", "owner_id": "u1", "binding_id": 42,
            "bot_type": "personal", "entity_id": "u1",
        }
        bind_repo = MagicMock()
        task_queue_service = MagicMock()

        svc = _make_service(
            bot_repository=bot_repo,
            device_binding_repo=bind_repo,
            device_provider=device_provider,
            baas_service_provider=lambda: baas,
            task_queue_service=task_queue_service,
        )
        svc._restart_bot_baas(
            bot_id="bot-1", user_id="u1", binding_id=42, bot=bot_repo.get_by_id_and_owner.return_value,
        )

        bot_repo.update_by_owner.assert_called_with(
            "bot-1", "u1", {"status": "PENDING"}
        )
        task_queue_service.enqueue.assert_not_called()
