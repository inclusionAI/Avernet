"""Public catalog service semantics for governed Repo assets."""

from agentclaw.community.core.skill_center.services.repository_catalog_service import (
    RepositoryCatalogService,
)


class _SkillService:
    def __init__(self) -> None:
        self.items = [
            {"id": "1", "name": "repo", "git_path": "git://ops/repo"},
            {"id": "2", "name": "center", "git_path": "center://published"},
        ]

    def list_git_skills(self, **_kwargs):
        # Historical latest/hottest cache can contain stale Center rows.
        return list(self.items)

    def search_market_skills(self, *_args, **_kwargs):
        return list(self.items)

    def get_skill(self, skill_id: str):
        return next((item for item in self.items if item["id"] == skill_id), None)

    def get_market_tree(self):
        return []


class _Factory:
    def __init__(self) -> None:
        self.service = _SkillService()

    def create(self):
        return self.service


def test_catalog_strictly_excludes_center_rows_from_list_search_and_detail() -> None:
    catalog = RepositoryCatalogService(_Factory())

    assert catalog.list(orderby="latest") == [
        {"id": "1", "name": "repo", "git_path": "git://ops/repo"}
    ]
    assert catalog.list(orderby="hotest") == [
        {"id": "1", "name": "repo", "git_path": "git://ops/repo"}
    ]
    assert catalog.search(keyword="", limit=100) == [
        {"id": "1", "name": "repo", "git_path": "git://ops/repo"}
    ]
    assert catalog.detail("2") is None
