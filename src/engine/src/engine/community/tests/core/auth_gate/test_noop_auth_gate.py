from __future__ import annotations

import pytest

from engine.community.plugin_api.auth_gate.models import VerifyResult
from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from engine.community.plugins.auth_gate.noop_impl import NoopAuthGateService


class TestNoopAuthGateService:
    @pytest.mark.asyncio
    async def test_verify_allows(self):
        svc = NoopAuthGateService()
        result = await svc.verify(token="t", content="hello", session_id="s1")
        assert isinstance(result, VerifyResult)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_switch_roundtrip(self):
        svc = NoopAuthGateService()
        assert await svc.get_switch() is False
        await svc.set_switch(True)
        assert await svc.get_switch() is True

    def test_satisfies_protocol(self):
        assert isinstance(NoopAuthGateService(), AuthGateService)
