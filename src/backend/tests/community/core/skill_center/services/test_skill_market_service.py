from agentclaw.community.api.skill_market_service import SkillMarketSearchQuery
from agentclaw.community.core.skill_center.services.skill_market_service import (
    SkillMarketService,
)


class _Repo:
    def __init__(self):
        self.calls = []

    def list_skills(self, user_id=None, bolt_id="default", env=None):
        self.calls.append((user_id, bolt_id))
        return [
            {"name": "Calendar", "description": "Dates", "git_path": "git://calendar"},
            {"name": "Mail", "description": "Calendar alerts", "git_path": "git://mail"},
            {"name": "Private", "git_path": "local://private"},
        ]


class _Cache:
    def __init__(self):
        self.value = None

    def get(self, key):
        return self.value

    def set(self, key, value):
        self.value = value
        return True


def test_search_filters_git_market_and_paginates():
    repo = _Repo()
    cache = _Cache()
    service = SkillMarketService(repo, cache)

    result = service.search(
        SkillMarketSearchQuery(keyword="calendar", page_num=2, page_size=1)
    )

    assert result.total == 2
    assert [item["name"] for item in result.items] == ["Mail"]
    assert repo.calls == [(None, None)]


def test_search_reuses_market_cache():
    repo = _Repo()
    cache = _Cache()
    service = SkillMarketService(repo, cache)

    first = service.search(SkillMarketSearchQuery(keyword="", page_num=1, page_size=20))
    second = service.search(SkillMarketSearchQuery(keyword="mail", page_num=1, page_size=20))

    assert first.total == 2
    assert second.total == 1
    assert repo.calls == [(None, None)]
