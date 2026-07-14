"""Unit tests for NoopMessageDispatcher."""

import pytest

from secbaas.community.api.bot_runtime import BotBindingInfo, BotChatContext
from secbaas.community.core.service.bot_run._noop_message_dispatcher import (
    NoopMessageDispatcher,
)


@pytest.fixture
def binding_info():
    return BotBindingInfo(
        bot_id="bot-1",
        entity_id="123",
        device_id="device-1",
        device_provider="arca",
        binding_id=1,
        bot_type="personal",
    )


@pytest.fixture
def context():
    return BotChatContext(
        api_key_prefix="key-abc",
        app_id="app-1",
        app_type="baas",
        tenant="test",
    )


class TestNoopMessageDispatcher:
    @pytest.mark.asyncio
    async def test_dispatch_send_no_op(self, binding_info, context):
        dispatcher = NoopMessageDispatcher()
        # Should not raise
        await dispatcher.dispatch_send(
            bot_service=None,
            run_id="run-001",
            session_id="sess-001",
            message="hello",
            binding_info=binding_info,
            context=context,
        )

    @pytest.mark.asyncio
    async def test_dispatch_inject_no_op(self, binding_info, context):
        dispatcher = NoopMessageDispatcher()
        # Should not raise
        await dispatcher.dispatch_inject(
            bot_service=None,
            run_id="run-001",
            session_id="sess-001",
            message="system instruction",
            binding_info=binding_info,
            context=context,
        )
