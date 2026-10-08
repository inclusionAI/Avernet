"""AICoding concern — community binding.

Binds ``WorkflowCatalogServiceProtocol`` to the empty ``NoopWorkflowCatalogService``
(the corp ``WorkflowCatalogService`` reads corp ``AntCodeConfig``, so it can't ship
to community). Mirrors ``CorpAICodingModule``'s protocol binding. Corp-free.
"""
from __future__ import annotations

from injector import Binder, Module, provider, singleton

from agentclaw.community.api.workflow_catalog_service import WorkflowCatalogServiceProtocol


class CommunityAICodingModule(Module):
    """community: empty workflow catalog (no AntCode)."""

    def configure(self, binder: Binder) -> None:
        return None

    @singleton
    @provider
    def workflow_catalog_service(self) -> WorkflowCatalogServiceProtocol:
        from agentclaw.community.plugins.community.aicoding import NoopWorkflowCatalogService

        return NoopWorkflowCatalogService()
