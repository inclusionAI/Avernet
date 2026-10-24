"""Preserve inner engine RPC deadlines at the public relay boundary."""
import pytest

from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)
from .test_relay import BOT, OWNER, _Transport, _relay


@pytest.mark.asyncio
async def test_engine_rpc_504_preserves_public_timeout_contract():
    transport = _Transport(raises=DeviceAdapterHTTPStatusError(504, "RPC timeout"))
    with pytest.raises(DeviceAdapterTimeoutError):
        await _relay(transport=transport).call(
            bot_id=BOT, owner_id=OWNER, stage="draft", method="GET",
            path="/api/engine/default-model",
        )
