from __future__ import annotations

from engine.community.plugin_api.notification.protocol import NotificationService
from engine.community.di.container import build_injector
from engine.community.di.profile import EngineProfile
from engine.community.di.runtime_mode import RuntimeConfig, RuntimeMode
from engine.community.tests._support import requires_corp


def _injector(profile: EngineProfile):
    return build_injector(config=RuntimeConfig(runtime=RuntimeMode.LOCAL, profile=profile))


def test_community_profile_provides_logger_notification():
    svc = _injector(EngineProfile.COMMUNITY).get(NotificationService)
    assert type(svc).__name__ == "LoggerNotificationService"


def test_test_profile_provides_logger_notification():
    svc = _injector(EngineProfile.TEST).get(NotificationService)
    assert type(svc).__name__ == "LoggerNotificationService"


@requires_corp
def test_corp_profile_provides_dingtalk_notification():
    svc = _injector(EngineProfile.CORP).get(NotificationService)
    assert type(svc).__name__ == "DingTalkNotificationService"
