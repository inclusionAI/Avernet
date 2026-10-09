"""Unit tests for CallerBotService and BotServiceSelector caller routing.

Covers:
- CallerBotService._require_sandbox: caller 模式必须显式带 sandbox_id
- 各 RPC 方法对内层 service 的委托与参数透传
- BotServiceSelector: caller provider 路由（未装配时退回 claw）
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bot_runtime import BotBindingInfo, BotServiceError
from secbaas.community.core.service.bot_run import BotServiceSelector
from secbaas.community.core.service.bot_run._caller_service import CallerBotService

BOT_ID = "bot-1"
ENTITY_ID = "entity-1"


def _binding(**overrides):
    defaults = {
        "bot_id": BOT_ID,
        "entity_id": ENTITY_ID,
        "sandbox_id": "sbx-1",
        "device_id": "dev-1",
        "device_provider": "caller",
    }
    defaults.update(overrides)
    return BotBindingInfo(**defaults)


# ==================== Tests: _require_sandbox ====================


class TestRequireSandbox:
    """caller 模式的容器由 caller-connection 预先拉起，必须显式带 sandbox_id。"""

    def test_missing_sandbox_raises(self):
        service = CallerBotService(MagicMock())
        with pytest.raises(BotServiceError, match="explicit sandbox_id"):
            service._require_sandbox(_binding(sandbox_id=None))

    def test_with_sandbox_returns_binding(self):
        service = CallerBotService(MagicMock())
        binding = _binding(sandbox_id="sbx-9")
        assert service._require_sandbox(binding) is binding


# ==================== Tests: delegation ====================


class TestDelegation:
    """各方法委托内层 service，入站前先做 sandbox 校验。"""

    async def test_create_session_delegates(self):
        inner = MagicMock()
        inner.create_session = AsyncMock(return_value="sess-info")
        service = CallerBotService(inner)
        binding = _binding()
        result = await service.create_session(
            bot_id=BOT_ID,
            session_id="s-1",
            metadata={},
            binding_info=binding,
            context=None,
        )
        assert result == "sess-info"
        inner.create_session.assert_awaited_once_with(
            bot_id=BOT_ID,
            session_id="s-1",
            metadata={},
            binding_info=binding,
            context=None,
            run_id=None,
        )

    async def test_create_session_without_sandbox_does_not_reach_inner(self):
        inner = MagicMock()
        inner.create_session = AsyncMock()
        service = CallerBotService(inner)
        with pytest.raises(BotServiceError, match="explicit sandbox_id"):
            await service.create_session(
                bot_id=BOT_ID,
                session_id="s-1",
                metadata={},
                binding_info=_binding(sandbox_id=None),
                context=None,
            )
        inner.create_session.assert_not_called()

    async def test_send_message_delegates(self):
        inner = MagicMock()
        inner.send_message = AsyncMock(return_value="resp")
        service = CallerBotService(inner)
        binding = _binding()
        result = await service.send_message(
            session_id="s-1",
            message="m",
            binding_info=binding,
            timeout=1.0,
            session_pending=True,
        )
        assert result == "resp"
        inner.send_message.assert_awaited_once_with(
            session_id="s-1",
            message="m",
            binding_info=binding,
            wait_result=True,
            context=None,
            timeout=1.0,
            chat_metadata=None,
            attachments=None,
            session_pending=True,
        )

    async def test_send_message_stream_delegates(self):
        captured: dict = {}

        async def _inner_stream(**kwargs):
            captured.update(kwargs)
            yield "chunk-1"

        inner = MagicMock()
        inner.send_message_stream = _inner_stream
        service = CallerBotService(inner)
        binding = _binding()
        chunks = [
            c
            async for c in service.send_message_stream(
                session_id="s-1",
                message="m",
                binding_info=binding,
                timeout=1.0,
            )
        ]
        assert chunks == ["chunk-1"]
        assert captured["binding_info"] is binding
        assert captured["session_pending"] is False

    async def test_inject_message_delegates(self):
        inner = MagicMock()
        inner.inject_message = AsyncMock()
        service = CallerBotService(inner)
        binding = _binding()
        await service.inject_message(
            session_id="s-1",
            message="m",
            binding_info=binding,
            attachments=["a"],
            session_pending=True,
        )
        inner.inject_message.assert_awaited_once_with(
            session_id="s-1",
            message="m",
            binding_info=binding,
            context=None,
            attachments=["a"],
            session_pending=True,
        )

    async def test_get_session_delegates(self):
        inner = MagicMock()
        inner.get_session = AsyncMock(return_value="sess")
        service = CallerBotService(inner)
        result = await service.get_session(session_id="s-1", binding_info=_binding())
        assert result == "sess"
        inner.get_session.assert_awaited_once()

    async def test_get_messages_delegates(self):
        inner = MagicMock()
        inner.get_messages = AsyncMock(return_value=["m1"])
        service = CallerBotService(inner)
        result = await service.get_messages(session_id="s-1", binding_info=_binding())
        assert result == ["m1"]
        inner.get_messages.assert_awaited_once()

    async def test_list_sessions_delegates(self):
        inner = MagicMock()
        inner.list_sessions = AsyncMock(return_value=["s1"])
        service = CallerBotService(inner)
        result = await service.list_sessions(binding_info=_binding(), limit=5)
        assert result == ["s1"]
        inner.list_sessions.assert_awaited_once_with(
            binding_info=_binding(), context=None, limit=5, offset=0
        )


# ==================== Tests: selector caller routing ====================


class TestSelectorCallerRouting:
    """BotServiceSelector 对 device_provider=caller 的路由。"""

    def test_caller_routes_to_caller_service(self):
        claw, baas, caller = MagicMock(), MagicMock(), MagicMock()
        selector = BotServiceSelector(
            claw_service=claw, baas_service=baas, caller_service=caller
        )
        assert selector.select(_binding()) is caller

    def test_caller_without_caller_service_falls_back_to_claw(self):
        claw, baas = MagicMock(), MagicMock()
        selector = BotServiceSelector(claw_service=claw, baas_service=baas)
        assert selector.select(_binding()) is claw

    def test_sandbox_validation_applies_to_routing_target(self):
        """caller 路由不依赖 sandbox 是否存在（校验在 service 内完成）。"""
        claw, baas, caller = MagicMock(), MagicMock(), MagicMock()
        selector = BotServiceSelector(
            claw_service=claw, baas_service=baas, caller_service=caller
        )
        assert selector.select(_binding(sandbox_id=None)) is caller
