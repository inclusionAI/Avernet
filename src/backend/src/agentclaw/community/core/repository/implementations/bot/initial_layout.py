"""Atomic Bot creation and Pool-native initial layout persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
)
from agentclaw.community.core.skills_pool.types import (
    InitialSkillLayoutSelection,
    SkillLayout,
    SkillLayoutPhase,
)


class BotInitialLayoutRepositoryMixin:
    """Keep the Bot row and initial layout in the same DB transaction."""

    _db: Any
    Model: Any

    def _new_bot_model(self, bot_data: dict[str, Any]) -> Any: ...

    def insert_with_initial_skill_layout(
        self,
        bot_data: dict[str, Any],
        *,
        layout: InitialSkillLayoutSelection | None,
    ) -> dict[str, Any]:
        with self._db.transactional_orm_session() as db:
            bot = self._new_bot_model(bot_data)
            db.add(bot)
            db.flush()
            if layout is not None:
                db.add(
                    BotSkillLayoutStateModel(
                        env=bot.env,
                        entity_id=bot.entity_id,
                        bot_id=bot.bot_id,
                        active_layout=SkillLayout.POOL.value,
                        target_layout=None,
                        phase=SkillLayoutPhase.POOL_INITIALIZING.value,
                        layout_contract_version=layout.layout_contract_version,
                        migration_generation=None,
                        preparation_id=None,
                        data_plane_cutover_committed=0,
                        rollout_evidence=json.dumps(
                            asdict(layout.rollout_evidence), ensure_ascii=False
                        ),
                    )
                )
                db.flush()
            return bot.to_dict()

__all__ = ["BotInitialLayoutRepositoryMixin"]
