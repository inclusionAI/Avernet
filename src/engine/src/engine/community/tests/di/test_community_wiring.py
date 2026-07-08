from __future__ import annotations

from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from engine.community.plugin_api.notification.protocol import NotificationService
from engine.community.di.container import build_injector
from engine.community.di.profile import EngineProfile
from engine.community.di.runtime_mode import RuntimeConfig, RuntimeMode


def test_community_profile_wires_noop_external_services():
    injector = build_injector(config=RuntimeConfig(runtime=RuntimeMode.LOCAL, profile=EngineProfile.COMMUNITY))
    assert type(injector.get(AuthGateService)).__name__ == "NoopAuthGateService"
    assert type(injector.get(NotificationService)).__name__ == "LoggerNotificationService"
