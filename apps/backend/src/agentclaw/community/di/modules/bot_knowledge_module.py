"""Composition for generic knowledge behavior; deployments override the catalog."""

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

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


class _CatalogGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    bot_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=128)
    env: str = Field(min_length=1, max_length=128)
    space_id: int = Field(gt=0)
    kb_ids: list[str]


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
            # Local acceptance substitutes only the external Plugin API, not
            # the HTTP/service/DB consumer chain. Never load this in production.
            fixture = os.environ.get("SINGLEBOX_ACCEPTANCE_KNOWLEDGE_FIXTURE_FILE", "")
            if self.profile == DeployProfile.SINGLEBOX and fixture:
                records = json.loads(Path(fixture).read_text(encoding="utf-8"))
                if not isinstance(records, list):
                    raise ValueError("knowledge catalog fixture must be a list")
                grants = {}
                for record in records:
                    grant = _CatalogGrant.model_validate(record)
                    key = (grant.bot_id, grant.owner_id, grant.env, grant.space_id)
                    if key in grants:
                        raise ValueError("duplicate knowledge catalog fixture scope")
                    grants[key] = tuple(grant.kb_ids)
                return LocalKnowledgeCatalog(grants)
            return LocalKnowledgeCatalog()
        return UnavailableKnowledgeCatalog()

    @provider
    @singleton
    @inject
    def service(
        self, repository: BotKnowledgeRepository, catalog: KnowledgeCatalogPlugin
    ) -> BotKnowledgeServiceProtocol:
        return BotKnowledgeService(repository, catalog)
