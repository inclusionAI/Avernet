"""Scope expert discovery without exposing internal source material."""

from dataclasses import dataclass

from agentclaw.community.core.expert_market.models import (
    ExpertNotFoundError,
    ExpertScope,
)
from agentclaw.community.core.expert_market.protocols import (
    ExpertCatalogRepositoryProtocol,
    ExpertCatalogServiceProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env


@dataclass(frozen=True)
class ExpertCatalogConfig:
    source_repo_id: str = "aiworkbench"


class ExpertCatalogService(ExpertCatalogServiceProtocol):
    def __init__(
        self, catalog: ExpertCatalogRepositoryProtocol, config: ExpertCatalogConfig
    ):
        self.catalog = catalog
        self.config = config

    def _scope(self, tenant):
        return ExpertScope(tenant, get_current_env(), self.config.source_repo_id)

    def search(self, *, tenant, keyword, category, sort, page_num, page_size):
        if (
            sort not in ("hot", "newest")
            or not 1 <= page_size <= 100
            or page_num < 1
            or len(keyword) > 200
        ):
            raise ValueError("invalid expert query")
        return self.catalog.search(
            self._scope(tenant),
            keyword=keyword,
            category=category,
            sort=sort,
            page_num=page_num,
            page_size=page_size,
        )

    def get(self, *, tenant, expert_id):
        value = self.catalog.get(self._scope(tenant), expert_id)
        if value is None:
            raise ExpertNotFoundError("expert is not available")
        return value

    def categories(self, *, tenant):
        return self.catalog.categories(self._scope(tenant))
