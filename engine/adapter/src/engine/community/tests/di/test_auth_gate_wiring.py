from __future__ import annotations

import pytest

from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from engine.community.di.container import build_injector
from engine.community.di.profile import EngineProfile
from engine.community.di.runtime_mode import RuntimeConfig, RuntimeMode
from engine.community.tests._support import requires_corp


def _injector(profile: EngineProfile):
    return build_injector(config=RuntimeConfig(runtime=RuntimeMode.LOCAL, profile=profile))


class TestAuthGateWiring:
    def test_community_profile_provides_auth_gate(self):
        svc = _injector(EngineProfile.COMMUNITY).get(AuthGateService)
        assert svc is not None
        assert type(svc).__name__ == "NoopAuthGateService"

    def test_test_profile_provides_auth_gate(self):
        svc = _injector(EngineProfile.TEST).get(AuthGateService)
        assert svc is not None
        assert type(svc).__name__ == "NoopAuthGateService"

    @requires_corp
    def test_corp_profile_is_distinct_not_community_fallback(self):
        svc = _injector(EngineProfile.CORP).get(AuthGateService)
        assert svc is not None
        assert type(svc).__name__ == "CorpZeroCheckAuthGateService"

    @pytest.mark.asyncio
    async def test_community_allows_all(self):
        svc = _injector(EngineProfile.COMMUNITY).get(AuthGateService)
        result = await svc.verify(token="t", content="c", session_id="s")
        assert result.allowed is True
