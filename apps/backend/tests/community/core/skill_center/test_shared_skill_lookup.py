"""``SkillQueryService`` as the apply engine's ``SharedSkillPort``: a Center skill by name, a repo skill by path.

The visibility rule itself is ``_resolve``'s, covered by ``test_skill_query_service``; here it is stubbed by
id so the lookup's own answers — one, none, several, not visible — are what is under test.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.ports.shared_skill_port import (
    SharedSkillAmbiguous,
    SharedSkillNotFound,
    SharedSkillPort,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.services.skill_query_service import SkillQueryService


class _Skills:
    def __init__(self, center: list[dict], repo: dict[str, dict]) -> None:
        self._center = center
        self._repo = repo
        self.asked: list[str] = []

    def list_center_by_name(self, name: str) -> list[dict]:
        self.asked.append(name)
        return [row for row in self._center if row["name"] == name]

    def get_by_git_path(self, git_path: str) -> dict | None:
        return self._repo.get(git_path)


def _service(*, center=(), repo=None, visible=frozenset()) -> SkillQueryService:
    service = SkillQueryService.__new__(SkillQueryService)
    service._skill_repo = _Skills(list(center), repo or {})
    calls = []

    def _resolve(*, skill_id, bot_id, owner_id, user_id):
        calls.append((skill_id, bot_id, owner_id, user_id))
        if skill_id not in visible:
            raise LocalSkillNotFoundError()
        return {}, {}, owner_id

    service._resolve = _resolve
    service.calls = calls
    return service


_WHO = dict(bot_id="b_1", owner_id="ent_1", actor_id="u_1")


def test_the_query_service_is_the_shared_skill_port() -> None:
    assert issubclass(SkillQueryService, SharedSkillPort)


def test_one_visible_center_skill_of_the_name_is_found_under_the_bot_and_principal() -> None:
    service = _service(center=[{"id": "7", "name": "refund-policy"}, {"id": "8", "name": "other"}], visible={"7", "8"})
    assert service.find_center_skill(**_WHO, name="refund-policy") == "7"
    assert service.calls == [("7", "b_1", "ent_1", "u_1")]


def test_a_center_skill_the_bot_cannot_use_counts_as_absent() -> None:
    service = _service(
        center=[{"id": "7", "name": "refund-policy"}, {"id": "9", "name": "refund-policy"}], visible={"9"}
    )
    assert service.find_center_skill(**_WHO, name="refund-policy") == "9"
    with pytest.raises(SharedSkillNotFound, match="added first"):
        _service(center=[{"id": "7", "name": "refund-policy"}]).find_center_skill(**_WHO, name="refund-policy")
    with pytest.raises(SharedSkillNotFound):
        _service().find_center_skill(**_WHO, name="refund-policy")


def test_several_visible_center_skills_of_one_name_are_ambiguous() -> None:
    service = _service(
        center=[{"id": "7", "name": "refund-policy"}, {"id": "9", "name": "refund-policy"}], visible={"7", "9"}
    )
    with pytest.raises(SharedSkillAmbiguous, match="2 Skill Center skills"):
        service.find_center_skill(**_WHO, name="refund-policy")


def test_a_repo_skill_is_found_by_its_git_path_when_the_bot_may_use_it() -> None:
    repo = {"git://team/weather": {"id": "4", "name": "weather"}}
    assert _service(repo=repo, visible={"4"}).find_repo_skill(**_WHO, name="weather", path="team/weather") == "4"
    with pytest.raises(SharedSkillNotFound, match="team/weather"):
        _service(repo=repo).find_repo_skill(**_WHO, name="weather", path="team/weather")
    with pytest.raises(SharedSkillNotFound):
        _service(repo=repo, visible={"4"}).find_repo_skill(**_WHO, name="weather", path="team/other")


def test_a_repo_skill_whose_runtime_name_differs_from_the_entry_is_refused() -> None:
    repo = {"git://team/weather": {"id": "4", "name": "weather"}}
    with pytest.raises(SharedSkillNotFound, match="is named 'weather', not 'forecast'"):
        _service(repo=repo, visible={"4"}).find_repo_skill(**_WHO, name="forecast", path="team/weather")
