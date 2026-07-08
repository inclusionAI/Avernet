from __future__ import annotations

from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from engine.community.plugin_api.notification.protocol import NotificationService
from engine.community.di.container import build_injector
from engine.community.di.profile import EngineProfile
from engine.community.di.runtime_mode import RuntimeConfig, RuntimeMode
from engine.community.tests._support import requires_corp


@requires_corp
def test_corp_profile_wires_corp_services_without_community_fallback():
    injector = build_injector(config=RuntimeConfig(runtime=RuntimeMode.LOCAL, profile=EngineProfile.CORP))
    assert type(injector.get(AuthGateService)).__name__ == "CorpZeroCheckAuthGateService"
    assert type(injector.get(NotificationService)).__name__ == "DingTalkNotificationService"
