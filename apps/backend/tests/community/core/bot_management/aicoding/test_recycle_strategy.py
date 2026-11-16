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
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
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
        audit_service=audit,
    )
    return bot, service, bots, passport, audit, factory, operations


@pytest.mark.parametrize("engine", ["openclaw", "hermes", "unregistered"])
def test_default_engines_keep_synchronous_recycle_and_never_resolve_queue(engine):
    bot, service, bots, passport, audit, factory, _ = facade(engine)
    result = service.submit(bot_id="b", owner_id="u", request_id="r")
    assert result["status"] == "RECYCLED" and result["changed"] is True
    assert "operation_id" not in result
    assert service.submit(bot_id="b", owner_id="u")["changed"] is False
    factory.assert_not_called()
    bots.stop_bot.assert_called_once()
    passport.freeze_agent_passport.assert_called_once()
    audit.record_openapi_recycle.assert_called_once_with(
        request_id="r", bot_id="b", owner_id="u"
    )


@pytest.mark.parametrize("engine", ["aicoding", "claude_code"])
def test_coding_strategy_owns_async_admission_without_calling_sync_release(engine):
    _, service, bots, passport, audit, factory, operations = facade(engine)
    operations.submit.return_value = {
        "status": "ACTIVE",
        "changed": True,
        "operation_id": "op",
        "phase": "QUEUED",
    }
    assert service.submit(bot_id="b", owner_id="u")["operation_id"] == "op"
    factory.assert_called_once()
    bots.stop_bot.assert_not_called()
    passport.freeze_agent_passport.assert_not_called()
    audit.record_openapi_recycle.assert_not_called()


def test_default_operation_query_is_not_found_without_constructing_coding_runtime():
    _, service, _, _, _, factory, _ = facade("openclaw")
    with pytest.raises(BotNotFoundError):
        service.get_operation(bot_id="b", owner_id="u", operation_id="op")
    factory.assert_not_called()


def test_coding_operation_query_delegates_to_engine_and_does_not_release():
    _, service, bots, _, _, _, operations = facade("claude_code")
    operations.get_operation.return_value = {"operation_id": "op", "phase": "FAILED"}
    assert (
        service.get_operation(bot_id="b", owner_id="u", operation_id="op")["phase"]
        == "FAILED"
    )
    operations.get_operation.assert_called_once_with(
        bot_id="b", owner_id="u", operation_id="op"
    )
    bots.stop_bot.assert_not_called()


def test_default_strategy_has_no_coding_state_policy():
    bot, *_ = facade("openclaw")
    bot["ext"]["recycle"] = {"phase": "BACKING_UP"}
    assert_lifecycle_mutable(
        bot
    )  # No global engine switch or coding journal interpretation.


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
        self.bot = {"bot_id": "b", "owner_id": "u", "entity_id": "u", "active_engine": "claude_code", "ext": {}}
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
        from agentclaw.community.core.bot_management.engines.aicoding.recycle_state import (
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


def test_default_engine_release_does_not_acquire_coding_lock():
    service = Service()
    service.bot["active_engine"] = "openclaw"
    service.release("b", "u")
    service.effect.assert_called_once()
    service._try_acquire_restart_lock.assert_not_called()
    service._restart_lock_repo.release.assert_not_called()
