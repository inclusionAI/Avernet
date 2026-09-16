"""Deployment-provided verification public key; absence disables mutation."""
import os
from injector import Module, provider, singleton
from engine.community.core.publish_ignore.protocol import PublishIgnoreService
from engine.community.plugins.publish_ignore import FilePublishIgnoreService


def publish_ignore_public_key() -> str:
    return os.environ.get("SERVICE_BOT_PUBLISH_IGNORE_VERIFY_KEY", "")


class PublishIgnoreModule(Module):
    @singleton
    @provider
    def service(self) -> PublishIgnoreService:
        return FilePublishIgnoreService(publish_ignore_public_key())
