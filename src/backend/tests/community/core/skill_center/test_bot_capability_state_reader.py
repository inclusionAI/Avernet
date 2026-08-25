"""The reader flushes first and answers from Installation alone."""

from __future__ import annotations

import pytest

from agentclaw.community.api.bot_capability_state_reader import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset

_BOT = {
    "bot_id": "bot-1",
    "owner_id": "owner",
    "entity_id": "entity-1",
    "active_engine": "openclaw",
    "env": "pre",
}


class _Bots:
    def __init__(self, bot: dict | None = _BOT) -> None:
        self._bot = bot
        self.lookups: list[tuple[str, str]] = []

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        self.lookups.append((bot_id, owner_id))
        return self._bot


class _Repository:
    def __init__(self) -> None:
        self.flush_calls: list[dict] = []
        self.mcp_reads: list[dict] = []

    def flush_installations(self, **kwargs) -> InstallationFlushPlan:
        self.flush_calls.append(kwargs)
        return InstallationFlushPlan(
            member_skill_ids=frozenset({1}),
            skills_to_install=frozenset({1}),
            skills_to_uninstall=frozenset(),
        )

    def list_installed_mcps(self, **kwargs) -> set[str]:
        # A read that beats the flush would answer from unflushed rows.
        assert self.flush_calls, "read reached Installation before the flush"
        self.mcp_reads.append(kwargs)
        return {"mcp.weather"}


class _PoolSkills:
    def __init__(self) -> None:
        self.reads: list[dict] = []
        self.flush_calls_at_read: int | None = None

    def bind(self, repository: _Repository) -> "_PoolSkills":
        self._repository = repository
        return self

    def list_bot_installed_assets(self, **kwargs) -> list[RegisteredSkillAsset]:
        assert self._repository.flush_calls, (
            "read reached Installation before the flush"
        )
        self.reads.append(kwargs)
        return [
            RegisteredSkillAsset(skill_id=1, name="qa", git_path="local://qa")
        ]


def _reader(
    *, bots: _Bots | None = None
) -> tuple[BotCapabilityStateReader, _Repository, _PoolSkills, _Bots]:
    repository = _Repository()
    pool_skills = _PoolSkills().bind(repository)
    bots = bots if bots is not None else _Bots()
    return (
        BotCapabilityStateReader(
            repository=repository, bot_repo=bots, pool_skills=pool_skills
        ),
        repository,
        pool_skills,
        bots,
    )


_EXPECTED_FLUSH = {
    "bot_id": "bot-1",
    "owner_id": "owner",
    "env": "pre",
    "engine_type": "openclaw",
    "default_engine_types": ("openclaw",),
}


def test_the_implementation_satisfies_the_public_protocol():
    reader, _repository, _pool, _bots = _reader()
    assert isinstance(reader, BotCapabilityStateReaderProtocol)


def test_skill_read_flushes_then_answers_from_the_installation_join():
    reader, repository, pool_skills, bots = _reader()

    assets = reader.active_skill_assets(bot_id="bot-1", owner_id="owner")

    assert repository.flush_calls == [_EXPECTED_FLUSH]
    assert pool_skills.reads == [
        {"env": "pre", "bot_id": "bot-1", "owner_id": "owner"}
    ]
    assert assets == (
        RegisteredSkillAsset(skill_id=1, name="qa", git_path="local://qa"),
    )
    assert bots.lookups == [("bot-1", "owner")]


def test_mcp_read_flushes_then_answers_from_installed_codes():
    reader, repository, _pool, _bots = _reader()

    codes = reader.active_mcp_server_codes(bot_id="bot-1", owner_id="owner")

    assert repository.flush_calls == [_EXPECTED_FLUSH]
    assert repository.mcp_reads == [{"bot_id": "bot-1", "owner_id": "owner"}]
    assert codes == frozenset({"mcp.weather"})


def test_a_caller_supplied_bot_row_skips_the_lookup():
    reader, repository, _pool, bots = _reader()

    reader.active_skill_assets(bot_id="bot-1", owner_id="owner", bot=_BOT)

    assert bots.lookups == []
    assert repository.flush_calls == [_EXPECTED_FLUSH]


def test_a_missing_bot_raises_before_any_flush():
    reader, repository, _pool, _bots = _reader(bots=_Bots(bot=None))

    with pytest.raises(LocalSkillNotFoundError):
        reader.active_skill_assets(bot_id="bot-1", owner_id="owner")
    with pytest.raises(LocalSkillNotFoundError):
        reader.active_mcp_server_codes(bot_id="bot-1", owner_id="owner")
    assert repository.flush_calls == []


def test_the_public_flush_addresses_the_bot_named_by_its_row():
    reader, repository, _pool, bots = _reader()

    plan = reader.flush(bot=_BOT)

    assert bots.lookups == []
    assert repository.flush_calls == [_EXPECTED_FLUSH]
    assert plan.skills_to_install == frozenset({1})
