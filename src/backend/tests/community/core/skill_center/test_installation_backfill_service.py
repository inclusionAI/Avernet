"""The backfill runs the flush deliberately, for one named Bot."""

from __future__ import annotations

import pytest

from agentclaw.community.api.installation_backfill_service import (
    InstallationBackfillServiceProtocol,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.services.installation_backfill_service import (
    InstallationBackfillService,
)

_BOT = {
    "bot_id": "bot-1",
    "owner_id": "owner",
    "entity_id": "entity-1",
    "active_engine": "openclaw",
    "env": "pre",
}

_EXPECTED_FLUSH = {
    "bot_id": "bot-1",
    "owner_id": "owner",
    "env": "pre",
    "engine_type": "openclaw",
    "default_engine_types": ("openclaw",),
}


class _Repository:
    def __init__(self) -> None:
        self.flush_calls: list[dict] = []

    def flush_installations(self, **kwargs) -> InstallationFlushPlan:
        self.flush_calls.append(kwargs)
        return InstallationFlushPlan(
            member_skill_ids=frozenset({1}),
            skills_to_install=frozenset({1}),
            skills_to_uninstall=frozenset(),
        )


class _Bots:
    def __init__(self, *, bot: dict | None = _BOT) -> None:
        self._bot = bot
        self.lookups: list[tuple[str, str]] = []

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        self.lookups.append((bot_id, owner_id))
        return self._bot


def _service(
    *, bots: _Bots | None = None
) -> tuple[InstallationBackfillService, _Repository, _Bots]:
    repository = _Repository()
    bots = bots if bots is not None else _Bots()
    return (
        InstallationBackfillService(repository=repository, bot_repo=bots),
        repository,
        bots,
    )


def test_the_implementation_satisfies_the_public_protocol():
    service, _repository, _bots = _service()
    assert isinstance(service, InstallationBackfillServiceProtocol)


def test_it_flushes_with_the_same_scope_the_reader_uses():
    service, repository, bots = _service()

    service.backfill_bot(bot_id="bot-1", owner_id="owner")

    assert repository.flush_calls == [_EXPECTED_FLUSH]
    assert bots.lookups == [("bot-1", "owner")]


def test_an_unknown_bot_is_an_error_not_a_silent_no_op():
    service, repository, _bots = _service(bots=_Bots(bot=None))

    with pytest.raises(LocalSkillNotFoundError):
        service.backfill_bot(bot_id="bot-1", owner_id="owner")

    assert repository.flush_calls == []


def test_the_layout_engine_wins_the_default_set_precedence():
    """A coding-template Bot resolves its Default Set by filesystem identity."""
    coding = {
        **_BOT,
        "active_engine": "claude_code",
        "template_type": "applicationCoding",
    }
    service, repository, _bots = _service(bots=_Bots(bot=coding))

    service.backfill_bot(bot_id="bot-1", owner_id="owner")

    assert repository.flush_calls[0]["engine_type"] == "claude_code"
    assert repository.flush_calls[0]["default_engine_types"] == (
        "aicoding",
        "claude_code",
    )
