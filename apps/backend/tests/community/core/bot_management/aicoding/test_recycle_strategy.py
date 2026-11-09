"""Recycling follows restart's engine strategy seam, not a global async policy."""

from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.bot_dormant.recycle_service import RecycleBotService
from agentclaw.community.core.bot_management.engines.recycle_contract import (
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
