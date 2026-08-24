"""The Bot Skill listing repairs Installation before it filters on it."""

from __future__ import annotations

import pytest

from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.services.local_skill_query_service import (
    LocalSkillQueryService,
)

_EMPTY = InstallationFlushPlan(
    member_skill_ids=frozenset(), skills_to_install=frozenset(), skills_to_uninstall=frozenset()
)

_BOT = {
    "bot_id": "bot",
    "owner_id": "owner",
    "env": "dev",
    "active_engine": "openclaw",
    "template_type": "",
}


class _Bots:
    def __init__(self, bot: dict | None = _BOT) -> None:
        self._bot = bot
        self.reads = 0

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        self.reads += 1
        return self._bot


class _SkillSets:
    """Stands in for the repository seam that owns resolution *and* repair."""

    def __init__(self, bridge: InstallationFlushPlan) -> None:
        self._bridge = bridge
        self.calls: list[dict] = []

    def flush_installations(self, **kwargs) -> InstallationFlushPlan:
        self.calls.append(kwargs)
        return self._bridge


class _Skills:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_bot_skills(self, **kwargs):
        self.calls.append(kwargs)
        return 0, []


class _Collaborators:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed

    def check_collaborator_permission(self, *_args) -> dict:
        return {"has_permission": self._allowed}


def _service(
    *,
    bridge: InstallationFlushPlan,
    bot: dict | None = _BOT,
    allowed: bool = True,
):
    skills, sets, bots = _Skills(), _SkillSets(bridge), _Bots(bot)
    service = LocalSkillQueryService(
        skill_repo=skills,
        bot_repo=bots,
        collaborator_service=_Collaborators(allowed),
        skill_sets=sets,
    )
    return service, skills, sets, bots


def _list(service, *, actor_id: str = "owner"):
    return service.list_bot_skills(
        bot_id="bot",
        owner_id="owner",
        actor_id=actor_id,
        page=1,
        page_size=20,
        active=None,
        keyword=None,
    )


def test_the_repair_runs_before_the_page_is_cut():
    """`active` is a filter, so the repair cannot happen after the query."""
    service, skills, sets, _bots = _service(
        bridge=InstallationFlushPlan(
            member_skill_ids=frozenset({1, 2}),
            skills_to_install=frozenset({1}),
            skills_to_uninstall=frozenset({2}),
        ),
    )

    _list(service)

    assert len(sets.calls) == 1
    assert skills.calls[0]["skill_set_member_ids"] == frozenset({1, 2})
    assert skills.calls[0]["bot_id"] == "bot"
    assert skills.calls[0]["user_id"] == "owner"


def test_the_skillset_scope_uses_the_bots_engine_and_layout_precedence():
    """Same Default-Set precedence the SkillSet surface applies."""
    service, _skills, sets, bots = _service(
        bridge=_EMPTY,
        bot={**_BOT, "active_engine": "claude_code", "template_type": "personalCoding"},
    )

    _list(service)

    assert sets.calls[0]["env"] == "dev"
    assert sets.calls[0]["engine_type"] == "claude_code"
    assert sets.calls[0]["default_engine_types"] == ("aicoding", "claude_code")
    # One Bot read for the whole listing: the engine comes off it.
    assert bots.reads == 1


def test_a_bot_with_no_recorded_engine_does_not_scope_to_a_literal_none():
    """A legacy null engine must widen the scope, not empty it.

    Formatting the column blindly yields the string "None", which matches no
    SkillSet at all — so every bridged Skill would vanish from the listing and
    every repair would be skipped, silently.
    """
    service, _skills, sets, _bots = _service(
        bridge=_EMPTY, bot={**_BOT, "active_engine": None}
    )

    _list(service)

    assert sets.calls[0]["engine_type"] is None
    assert sets.calls[0]["default_engine_types"] == ()



def test_an_invisible_bot_is_refused_before_anything_is_written():
    """An actor who cannot see the Bot cannot cause a write against it."""
    service, skills, sets, _bots = _service(bridge=_EMPTY, bot=None)

    with pytest.raises(LocalSkillNotFoundError):
        _list(service)

    assert sets.calls == [] and skills.calls == []


def test_a_collaborator_without_permission_is_refused_before_any_write():
    service, skills, sets, _bots = _service(bridge=_EMPTY, allowed=False)

    with pytest.raises(LocalSkillNotFoundError):
        _list(service, actor_id="someone-else")

    assert sets.calls == [] and skills.calls == []
