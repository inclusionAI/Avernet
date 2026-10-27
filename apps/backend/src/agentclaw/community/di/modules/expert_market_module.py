"""Expert directory assembly shared by community and enterprise profiles."""

import re

from injector import Module, provider, singleton

from agentclaw.community.api.expert_catalog_service import ExpertCatalogServiceProtocol
from agentclaw.community.core.expert_market.repository import ExpertCatalogRepository
from agentclaw.community.core.expert_market.scanner import ExpertScanner
from agentclaw.community.core.expert_market.service import (
    ExpertCatalogConfig,
    ExpertCatalogService,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class ExpertMarketModule(Module):
    @singleton
    @provider
    def expert_catalog_config(self) -> ExpertCatalogConfig:
        from agentclaw.community.di.modules.config_module import read_user_config

        source_id = (read_user_config().get("git_sync", {}) or {}).get(
            "expert_source_repo_id", "aiworkbench"
        )
        if not isinstance(source_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{1,64}", source_id
        ):
            raise ValueError("invalid configured expert source repository identifier")
        return ExpertCatalogConfig(source_id)

    @singleton
    @provider
    def expert_catalog(self, db: DatabasePlugin) -> ExpertCatalogRepository:
        return ExpertCatalogRepository(db)

    @singleton
    @provider
    def expert_scanner(self, catalog: ExpertCatalogRepository) -> ExpertScanner:
        return ExpertScanner(catalog)

    @singleton
    @provider
    def expert_catalog_service(
        self, catalog: ExpertCatalogRepository, config: ExpertCatalogConfig
    ) -> ExpertCatalogServiceProtocol:
        return ExpertCatalogService(catalog, config)
