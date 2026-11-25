"""Recycling follows restart's engine strategy seam, not a global async policy."""

from unittest.mock import MagicMock

from types import SimpleNamespace
from unittest.mock import Mock
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    held_resource_lock,
    serialize_resource_release,
)
from agentclaw.community.utils.env_utils import get_current_env

import pytest
from agentclaw.community.core.bot_dormant.recycle_service import RecycleBotService
from agentclaw.community.core.bot_management.engines.restart_contract import (
    RecycleServices,
)
from agentclaw.community.core.bot_management.engines.registry import (
    assert_lifecycle_mutable,
    resolve_restart_strategy,
)


def facade(engine):
    bot = {
        "bot_id": "b",
        "owner_id": "u",
        "bot_type": "personal",
        "active_engine": engine,
        "status": "ACTIVE",
        "ext": {},
    }
    bots, passport, audit, operations = (MagicMock() for _ in range(4))
    bots.get_bot.side_effect = lambda **_: dict(bot)
    bots.is_teclaw_bot.return_value = False
    bots.update_status.side_effect = lambda **kwargs: bot.update(
        status=kwargs["status"]
    )
    factory = MagicMock(return_value=operations)
    service = RecycleBotService(
        bots,
        passport,
        recycle_services=RecycleServices(operations=factory),
    )
    return bot, service, bots, passport, audit, factory, operations


def test_default_engine_strategy_does_not_request_async_recycle():
    ctx, strategy = resolve_restart_strategy({
        "bot_id": "b", "owner_id": "u", "bot_type": "personal",
        "active_engine": "openclaw",
    })

    assert strategy.should_async_recycle(ctx) is False


def test_aicoding_recycle_admission_reuses_fastdisk_detector_only(monkeypatch):
    from agentclaw.community.core.bot_management.engines.aicoding import restart_backup
    from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
        AicodingProvisioningStrategy,
    )

    devices = MagicMock()
    devices.get_device.return_value = SimpleNamespace(
        device_id="device-1", device_provider="local", device_props={}, status="ACTIVE",
    )
    devices.exec_shell_new.return_value = SimpleNamespace(exit_code=0, stdout="{}")
    detected = MagicMock(side_effect=lambda execute, **_: (execute("probe"), True)[1])
    monkeypatch.setattr(restart_backup, "_detect_fastdisk", detected)
    config = MagicMock()
    strategy = AicodingProvisioningStrategy(
        "claude_code", common_config_provider=lambda: config,
    )
    ctx = SimpleNamespace(bot_id="b", owner_id="u")

    assert strategy.should_async_recycle(
        ctx,
        binding_id=1,
        device_service_provider=lambda: devices,
        operation_id="admission-op",
    ) is True
    detected.assert_called_once()
    config.assert_not_called()
    devices.exec_shell_new.assert_called_once()


def test_aicoding_recycle_detection_failure_falls_back_to_sync(monkeypatch):
    from agentclaw.community.core.bot_management.engines.aicoding import restart_backup
    from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
        AicodingProvisioningStrategy,
    )

    devices = MagicMock()
    devices.get_device.return_value = SimpleNamespace(
        device_id="device-1", device_provider="local", device_props={}, status="ACTIVE",
    )
    monkeypatch.setattr(
        restart_backup, "_detect_fastdisk", MagicMock(side_effect=RuntimeError("probe")),
    )
    config = MagicMock()
    strategy = AicodingProvisioningStrategy(
        "aicoding", common_config_provider=lambda: config,
    )
    ctx = SimpleNamespace(bot_id="b", owner_id="u")

    assert strategy.should_async_recycle(
        ctx,
        binding_id=1,
        device_service_provider=lambda: devices,
    ) is False
    config.assert_not_called()


def test_sync_recycle_marker_runs_original_recycle_without_queue():
    _, service, bots, passport, _, factory, operations = facade("openclaw")
    operations.submit.return_value = {
        "_synchronous": True,
        "bot_id": "b",
        "owner_id": "u",
        "status": "ACTIVE",
        "changed": True,
    }
    result = service.submit(bot_id="b", owner_id="u")

    assert result == {
        "bot_id": "b",
        "owner_id": "u",
        "status": "RECYCLED",
        "changed": True,
        "operation_id": None,
        "phase": None,
    }
    factory.assert_called_once()
    operations.submit.assert_called_once()
    bots.stop_bot.assert_called_once()
    bots.update_status.assert_called_once_with(
        bot_id="b", user_id="u", status="RECYCLED"
    )
    passport.freeze_agent_passport.assert_called_once()


@pytest.mark.parametrize("engine", ["openclaw", "hermes", "unregistered", "aicoding", "claude_code"])
def test_all_personal_engines_use_the_same_async_recycle(engine):
    _, service, bots, passport, audit, factory, operations = facade(engine)
    operations.submit.return_value = {
        "status": "ACTIVE", "changed": True, "operation_id": "op", "phase": "QUEUED",
    }
    assert service.submit(bot_id="b", owner_id="u")["operation_id"] == "op"
    factory.assert_called_once()
    bots.stop_bot.assert_not_called()
    passport.freeze_agent_passport.assert_not_called()
    audit.record_openapi_recycle.assert_not_called()


@pytest.mark.parametrize("engine", ["openclaw", "claude_code"])
def test_all_personal_engines_share_the_operation_query(engine):
    _, service, bots, _, _, _, operations = facade(engine)
    operations.get_operation.return_value = {"operation_id": "op", "phase": "FAILED"}
    assert service.get_operation(bot_id="b", owner_id="u", operation_id="op")["phase"] == "FAILED"
    operations.get_operation.assert_called_once_with(bot_id="b", owner_id="u", operation_id="op")
    bots.stop_bot.assert_not_called()


@pytest.mark.parametrize("engine", ["openclaw", "claude_code"])
def test_all_personal_engines_reject_lifecycle_changes_during_recycle(engine):
    bot, *_ = facade(engine)
    bot["ext"]["recycle"] = {"phase": "BACKING_UP"}
    with pytest.raises(BotInvalidLifecycleStateError):
        assert_lifecycle_mutable(bot)


def test_shared_recycle_sources_do_not_depend_on_coding_implementation():
    import ast
    from pathlib import Path

    from agentclaw.community.core.bot_dormant import recycle_service

    source_root = Path(recycle_service.__file__).parent
    for source in source_root.glob("*.py"):
        tree = ast.parse(source.read_text())
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        assert not any("engines.aicoding" in module for module in imports), source.name


def test_shared_device_service_surface_has_no_recycle_specific_methods():
    from agentclaw.community.core.devices.device_service_protocol import (
        DeviceServiceProtocol,
    )
    from agentclaw.community.core.devices.services.device_service import DeviceService

    for service in (DeviceService, DeviceServiceProtocol):
        assert not hasattr(service, "submit_recycle_release")
        assert not hasattr(service, "observe_recycle_release")


class Service:
    def __init__(self):
        self.bot = {"bot_type": "personal", "bot_id": "b", "owner_id": "u", "entity_id": "u", "active_engine": "claude_code", "ext": {}}
        self._repository = SimpleNamespace(
            get_by_id_and_owner=Mock(side_effect=lambda *_: self.bot)
        )
        self._restart_lock_repo = Mock()
        self._try_acquire_restart_lock = Mock(
            return_value=SimpleNamespace(lock_token="token")
        )
        self.effect = Mock()

    @serialize_resource_release
    def release(self, bot_id, user_id):
        from agentclaw.community.core.bot_management.engines.restart_contract import (
            assert_not_recycling,
        )

        assert_not_recycling(self.bot)
        self.effect()


def test_release_serializes_and_releases_after_effect():
    service = Service()
    service.release("b", "u")
    service.effect.assert_called_once()
    service._try_acquire_restart_lock.assert_called_once_with(
        get_current_env(), "u", "b", "u"
    )
    service._restart_lock_repo.release.assert_called_once_with(
        get_current_env(), "u", "b", "token"
    )
    assert held_resource_lock.get() is None


def test_recycle_committed_before_lock_is_rechecked():
    service = Service()

    def acquire(*_):
        service.bot["ext"]["recycle"] = {"phase": "QUEUED"}
        return SimpleNamespace(lock_token="token")

    service._try_acquire_restart_lock.side_effect = acquire
    with pytest.raises(BotInvalidLifecycleStateError):
        service.release("b", "u")
    service.effect.assert_not_called()
    service._restart_lock_repo.release.assert_called_once()


def test_existing_restart_scope_does_not_reacquire_or_release_lock():
    service = Service()
    token = held_resource_lock.set((get_current_env(), "u", "b", "u"))
    try:
        service.release("b", "u")
    finally:
        held_resource_lock.reset(token)
    service.effect.assert_called_once()
    service._try_acquire_restart_lock.assert_not_called()
    service._restart_lock_repo.release.assert_not_called()


def test_busy_release_has_no_side_effect():
    service = Service()
    service._try_acquire_restart_lock.return_value = None
    with pytest.raises(BotInvalidLifecycleStateError):
        service.release("b", "u")
    service.effect.assert_not_called()


def test_default_engine_release_uses_shared_recycle_admission_lock():
    service = Service()
    service.bot["active_engine"] = "openclaw"
    service.release("b", "u")
    service.effect.assert_called_once()
    service._try_acquire_restart_lock.assert_called_once()
    service._restart_lock_repo.release.assert_called_once()


@pytest.mark.parametrize("ext,blocked", [
    ("{", False), ("null", False), ("[]", False), (None, False),
    ('{"recycle":{"phase":"RELEASING"}}', True),
    ({"recycle": {"phase": "NEEDS_CONFIRMATION"}}, True),
])
def test_shared_recycle_guard_accepts_legacy_ext_without_hiding_valid_operations(ext, blocked):
    bot, *_ = facade("openclaw")
    bot["ext"] = ext
    if blocked:
        with pytest.raises(BotInvalidLifecycleStateError):
            assert_lifecycle_mutable(bot)
    else:
        assert_lifecycle_mutable(bot)


def test_shared_recycle_guard_does_not_hide_non_syntax_decode_errors(monkeypatch):
    from agentclaw.community.core.bot_management.engines import restart_contract

    bot, *_ = facade("openclaw")
    bot["ext"] = '{"recycle":{"phase":"RELEASING"}}'
    monkeypatch.setattr(
        restart_contract.json, "loads", Mock(side_effect=ValueError("decode limit"))
    )
    with pytest.raises(ValueError, match="decode limit"):
        assert_lifecycle_mutable(bot)


@pytest.mark.parametrize("engine", ["openclaw", "claude_code"])
@pytest.mark.parametrize("bot_type", ["service", "desktop"])
def test_non_personal_release_does_not_acquire_recycle_lock(engine, bot_type):
    service = Service()
    service.bot.update(active_engine=engine, bot_type=bot_type)
    service.release("b", "u")
    service.effect.assert_called_once()
    service._try_acquire_restart_lock.assert_not_called()
    service._restart_lock_repo.release.assert_not_called()


@pytest.mark.parametrize("engine", ["openclaw", "claude_code"])
@pytest.mark.parametrize("bot_type", ["service", "desktop"])
def test_non_personal_lifecycle_does_not_read_personal_recycle_state(engine, bot_type):
    bot, *_ = facade(engine)
    bot.update(bot_type=bot_type, ext={"recycle": {"phase": "QUEUED"}})
    assert_lifecycle_mutable(bot)
