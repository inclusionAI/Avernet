"""Unit tests for the gateway message result route."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.adapters.web.routers.gateway.dependencies import (
    GatewayAuthContext,
)
from secbaas.community.adapters.web.routers.gateway.message_router import (
    get_message_result,
)
from secbaas.community.core.repository.bot_run import BotRunRecord


def _make_run_record(**overrides):
    defaults = {
        "id": 1,
        "gmt_create": datetime.now(),
        "gmt_modified": datetime.now(),
        "run_id": "msg-001",
        "bot_id": "bot-1:entity-1",
        "api_key_prefix": "kp-001",
        "message": "hello",
        "message_long": "hello",
        "metadata": {"session_id": None},
        "status": "COMPLETED",
        "result_content": "reply",
        "result_content_long": "reply",
        "result_extra": {"session_id": "sess-from-extra", "usage": None},
        "error": None,
        "completed_at": datetime.now(),
    }
    defaults.update(overrides)
    return BotRunRecord(**defaults)


@pytest.mark.asyncio
async def test_message_result_uses_valid_session_id_when_metadata_is_null():
    auth_ctx = GatewayAuthContext(
        resource_key="resource-key",
        resource_key_id=1,
        tenant="tenant",
        app="app",
    )
    bot_runner = MagicMock()
    bot_runner.get_result.return_value = _make_run_record()
    repository = MagicMock()

    with patch(
        "secbaas.community.adapters.web.routers.gateway.message_router.check_bot_access",
        return_value="bot-1:entity-1",
    ):
        result = await get_message_result(
            message_id="msg-001",
            auth_ctx=auth_ctx,
            bot_runner=bot_runner,
            resource_key_repository=repository,
        )

    assert result.data.session_id == "sess-from-extra"
