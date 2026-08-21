from __future__ import annotations

import pytest

from agentclaw.community.core.skills_pool.participation import (
    BotSkillLayoutStateParticipationResolver,
    SkillLayoutParticipation,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.di.modules.skills_pool_module import SkillsPoolModule


SCOPE = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")


class _Layouts:
    def __init__(
        self,
        *,
        active_layout: SkillLayout = SkillLayout.LEGACY,
        target_layout: SkillLayout | None = None,
    ) -> None:
        self.state = BotSkillLayoutState(
            scope=SCOPE,
            active_layout=active_layout,
            target_layout=target_layout,
            phase=SkillLayoutPhase.LEGACY_ACTIVE,
            migration_generation=None,
            persisted=active_layout is SkillLayout.POOL or target_layout is not None,
        )

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        assert scope == SCOPE
        return self.state


def _resolver(layouts: _Layouts) -> BotSkillLayoutStateParticipationResolver:
    return BotSkillLayoutStateParticipationResolver(layout_repository=layouts)


@pytest.mark.parametrize(
    ("active_layout", "target_layout"),
    [
        (SkillLayout.POOL, None),
        (SkillLayout.LEGACY, SkillLayout.POOL),
    ],
)
def test_pool_or_transitioning_bot_participates_in_edit_lock(
    active_layout: SkillLayout, target_layout: SkillLayout | None
) -> None:
    result = _resolver(
        _Layouts(active_layout=active_layout, target_layout=target_layout)
    ).resolve(scope=SCOPE)

    assert result == SkillLayoutParticipation(
        participates_in_pool_layout=True,
        label="pool_layout_state",
    )


def test_legacy_bot_does_not_participate_in_pool_edit_lock() -> None:
    result = _resolver(_Layouts()).resolve(scope=SCOPE)

    assert result == SkillLayoutParticipation(
        participates_in_pool_layout=False,
        label="legacy_layout_state",
    )


def test_module_wires_layout_state_as_the_participation_source() -> None:
    resolver = SkillsPoolModule().skill_layout_participation_resolver(
        _Layouts(target_layout=SkillLayout.POOL)
    )

    result = resolver.resolve(scope=SCOPE)

    assert result == SkillLayoutParticipation(
        participates_in_pool_layout=True,
        label="pool_layout_state",
    )
