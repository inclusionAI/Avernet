"""Unit tests for the relay session pairing registry and server."""

from __future__ import annotations

import asyncio

import pytest

from sandboxproxy.community.core.relay import RelayRegistry


class TestRelayRegistry:
    @pytest.mark.asyncio
    async def test_register_and_connect(self) -> None:
        reg = RelayRegistry(wait_timeout=30.0)
        reg.register_mng("sess-1")
        fut = reg.connect_client("sess-1")
        assert fut is not None
        assert reg.connect_client("sess-1") is None

    def test_no_waiting_mng(self) -> None:
        reg = RelayRegistry()
        assert reg.connect_client("missing") is None

    @pytest.mark.asyncio
    async def test_cleanup_expired(self) -> None:
        reg = RelayRegistry(wait_timeout=0.01)
        reg.register_mng("sess-old")
        await asyncio.sleep(0.03)
        reg.register_mng("sess-new")
        expired = reg.cleanup_expired()
        assert "sess-old" in expired
        assert "sess-new" not in expired

    def test_cleanup_empty(self) -> None:
        reg = RelayRegistry()
        assert reg.cleanup_expired() == []

    @pytest.mark.asyncio
    async def test_signal_mng_ready(self) -> None:
        reg = RelayRegistry()
        fut = reg.register_mng("sess-1")
        ws = object()
        reg.signal_mng_ready("sess-1", ws)
        assert await asyncio.wait_for(fut, timeout=1.0) is ws
