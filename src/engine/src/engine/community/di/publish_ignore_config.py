"""Publish-ignore service wiring; authentication uses the existing runtime ingress."""
from injector import Module, provider, singleton
from engine.community.core.publish_ignore.protocol import PublishIgnoreService
from engine.community.plugins.publish_ignore import FilePublishIgnoreService


class PublishIgnoreModule(Module):
    @singleton
    @provider
    def service(self) -> PublishIgnoreService:
        return FilePublishIgnoreService()
