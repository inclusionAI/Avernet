"""Composition for generic knowledge behavior; deployments override the catalog."""

from injector import Module, inject, provider, singleton
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugins.community.knowledge_catalog import (
    UnavailableKnowledgeCatalog,
)
from agentclaw.community.plugins.local.knowledge_catalog import LocalKnowledgeCatalog
from agentclaw.community.api.bot_knowledge_service import BotKnowledgeServiceProtocol
from agentclaw.community.core.bot_knowledge.repository.store import (
    BotKnowledgeRepository,
)
from agentclaw.community.core.bot_knowledge.service import BotKnowledgeService
from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin


class BotKnowledgeModule(Module):
    def __init__(self, profile: DeployProfile):
        self.profile = profile

    @provider
    @singleton
    def catalog(self) -> KnowledgeCatalogPlugin:
        if self.profile in {
            DeployProfile.TEST,
            DeployProfile.SINGLEBOX,
            DeployProfile.CORP_TEST,
        }:
            return LocalKnowledgeCatalog()
        return UnavailableKnowledgeCatalog()

    @provider
    @singleton
    @inject
    def service(
        self, repository: BotKnowledgeRepository, catalog: KnowledgeCatalogPlugin
    ) -> BotKnowledgeServiceProtocol:
        return BotKnowledgeService(repository, catalog)
