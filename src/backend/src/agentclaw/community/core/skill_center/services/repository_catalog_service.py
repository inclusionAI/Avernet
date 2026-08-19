"""One application service for the governed aiworkbench Repo catalog."""
from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.skill_center.constants import LOCK_HELD_ERRORS
from agentclaw.community.core.skill_center.factories import SkillServiceFactory


class RepositoryCatalogService:
    """Preserve the legacy market's cache, filesystem tree, and sync semantics."""

    @inject
    def __init__(self, factory: SkillServiceFactory) -> None:
        self._factory = factory

    def list(self, *, path: str | None = None, orderby: str | None = None) -> list[dict[str, Any]]:
        return self._factory.create().list_git_skills(path=path, bolt_id=None, orderby=orderby)

    def list_page(
        self,
        *,
        path: str | None = None,
        orderby: str | None = None,
        keyword: str = "",
        page: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Apply the catalog's filter and pagination as one service operation."""
        items = self.list(path=path, orderby=orderby)
        if keyword:
            lowered = keyword.lower()
            items = [
                item
                for item in items
                if lowered
                in " ".join(
                    str(item.get(key) or "")
                    for key in ("name", "description", "category")
                ).lower()
            ]
        start = (page - 1) * page_size
        return len(items), list(items[start : start + page_size])

    def search(self, *, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._factory.create().search_market_skills(keyword, limit=limit)

    def tree(self) -> list[dict[str, Any]]:
        return self._factory.create().get_market_tree()

    def detail(self, skill_id: str) -> dict[str, Any] | None:
        skill = self._factory.create().get_skill(skill_id)
        return skill if skill and str(skill.get("git_path") or "").startswith("git://") else None

    def sync(self) -> dict[str, Any]:
        """Run GitSyncService's single full fetch/extract/scan/cache operation."""
        result = self._factory.create().sync_repo_with_lock(min_interval=300)
        if result.get("error") in LOCK_HELD_ERRORS:
            return {"status": "in_progress"}
        if not result.get("success"):
            return {"status": "failed", "message": result.get("message", "Sync failed")}
        return {"status": "completed", "result": result}
