from __future__ import annotations

from injector import Module, provider, singleton

from engine.community.plugin_api.notification.protocol import NotificationService
from engine.community.plugins.notification.logger_impl import LoggerNotificationService


class CommunityNotificationModule(Module):
    @singleton
    @provider
    def notification_service(self) -> NotificationService:
        return LoggerNotificationService()
