"""COMMUNITY DI binding for the work-item service (unsupported → 501)."""
from __future__ import annotations

from injector import Module, provider, singleton

from engine.community.plugin_api.work_item.protocol import WorkItemService
from engine.community.plugins.work_item import NoopWorkItemService


class CommunityWorkItemModule(Module):
    @singleton
    @provider
    def work_item_service(self) -> WorkItemService:
        return NoopWorkItemService()
