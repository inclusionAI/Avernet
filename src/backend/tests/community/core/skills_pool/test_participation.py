from __future__ import annotations

import pytest

from agentclaw.community.core.skills_pool.participation import (
    BotEngineSkillLayoutParticipationResolver,
    SkillLayoutParticipation,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.di.modules.skills_pool_module import SkillsPoolModule


SCOPE = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
DEFAULT = SkillLayoutParticipation(
    participates_in_pool_layout=True,
    label="default_pool_layout",
)
NO_POOL = SkillLayoutParticipation(
    participates_in_pool_layout=False,
    label="artifact_layout",
)


class _Bots:
    def __init__(self, bot: dict | None) -> None:
        self._bot = bot

    def get_by_id_and_entity(self, bot_id: str, entity_id: str) -> dict | None:
        assert (bot_id, entity_id) == (SCOPE.bot_id, SCOPE.entity_id)
        return self._bot


def _resolver(bot: dict | None) -> BotEngineSkillLayoutParticipationResolver:
    return BotEngineSkillLayoutParticipationResolver(
        bot_repository=_Bots(bot),
        default=DEFAULT,
        by_engine={"artifact_engine": NO_POOL},
    )


def test_explicit_engine_policy_controls_layout_participation() -> None:
    result = _resolver({"env": "pre", "active_engine": "artifact_engine"}).resolve(
        scope=SCOPE
    )

    assert result is NO_POOL


@pytest.mark.parametrize(
    "bot",
    [
        None,
        {"env": "prod", "active_engine": "artifact_engine"},
        {"env": "pre"},
        {"env": "pre", "active_engine": 123},
        {"env": "pre", "active_engine": "unknown_engine"},
    ],
)
def test_unknown_or_incomplete_bot_keeps_conservative_default(bot: dict | None) -> None:
    result = _resolver(bot).resolve(scope=SCOPE)

    assert result is DEFAULT


def test_module_wires_artifact_engine_policy_outside_shared_guard() -> None:
    resolver = SkillsPoolModule().skill_layout_participation_resolver(
        _Bots({"env": "pre", "active_engine": "teclaw"})
    )

    result = resolver.resolve(scope=SCOPE)

    assert result == SkillLayoutParticipation(
        participates_in_pool_layout=False,
        label="teclaw_no_pool_layout",
    )
