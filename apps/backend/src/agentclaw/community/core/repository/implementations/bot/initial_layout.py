"""Atomic Bot creation and Pool-native initial layout persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
)
from agentclaw.community.core.spaces.errors import SpaceNotFoundError
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.utils.env_utils import get_current_env
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

    def insert(self, bot_data: dict[str, Any]) -> dict[str, Any]:
        with self._db.orm_session() as db:
            bot = self._new_bot_model(bot_data)
            db.add(bot)
            db.flush()
            return bot.to_dict()

    def insert_with_initial_skill_layout(
        self,
        bot_data: dict[str, Any],
        *,
        layout: InitialSkillLayoutSelection | None,
    ) -> dict[str, Any]:
        with self._db.transactional_orm_session() as db:
            # Serialize with delete_team_space's row lock when the Bot is
            # created into a Space: without FOR UPDATE a concurrent soft-delete
            # can commit between the service-level quota/membership checks and
            # this insert, leaving a live Bot assigned to a soft-deleted Space
            # even though the deletion's emptiness recheck passed.
            space_id = bot_data.get("space_id")
            if space_id is not None:
                target_space = (
                    db.query(SpaceModel)
                    .filter(
                        SpaceModel.id == space_id,
                        SpaceModel.env == (bot_data.get("env") or get_current_env()),
                        SpaceModel.deleted_at.is_(None),
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if target_space is None:
                    raise SpaceNotFoundError(f"space {space_id} not found")
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
