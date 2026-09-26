"""Integration tests — relay pairing via the RelayServer against a stub client."""

from __future__ import annotations

import pytest

from sandboxproxy.community.core.relay import RelayServer
from sandboxproxy.community.plugins.relay_client.stub import StubRelayClient


@pytest.mark.integration
class TestRelayPairing:
    @pytest.mark.asyncio
    async def test_mng_first_then_client(self) -> None:
        relay = RelayServer(StubRelayClient(), wait_timeout=30.0)
        await relay.start()

        # mng registers and writes active route
        assert await relay.register_mng("sess-1") is True
        relay.signal_mng_ready("sess-1", mng_ws := object())

        # client connects and receives the mng websocket
        fut = await relay.connect_client("sess-1")
        assert fut is not None
        assert await fut is mng_ws

        await relay.close_session("sess-1")
        await relay.shutdown()

    @pytest.mark.asyncio
    async def test_client_before_mng_returns_none(self) -> None:
        relay = RelayServer(StubRelayClient(), wait_timeout=30.0)
        await relay.start()
        fut = await relay.connect_client("sess-1")
        assert fut is None
        await relay.shutdown()

    @pytest.mark.asyncio
    async def test_route_write_failure_blocks_mng(self) -> None:
        class FailingRelayClient(StubRelayClient):
            async def upsert_route_active(self, session_id: str) -> bool:
                return False

        relay = RelayServer(FailingRelayClient(), wait_timeout=30.0)
        await relay.start()
        assert await relay.register_mng("sess-1") is False
        await relay.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_marks_open_routes_closed(self) -> None:
        stub = StubRelayClient()
        relay = RelayServer(stub, wait_timeout=30.0)
        await relay.start()
        await relay.register_mng("sess-1")
        await relay.shutdown()
        assert await stub.get_route_info("sess-1") is not None
