"""Atomic Bot creation and Pool-native initial layout persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import func

from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
)
from agentclaw.community.core.skills_pool.types import (
    InitialSkillLayoutSelection,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.utils.env_utils import get_current_env


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

    def soft_delete_failed_creation(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> bool:
        env = get_current_env()
        with self._db.transactional_orm_session() as db:
            bot = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self.Model.env == env,
                )
                .one_or_none()
            )
            if bot is None:
                return False
            bot.is_delete = 1
            bot.gmt_modified = func.now()
            db.query(BotSkillLayoutStateModel).filter(
                BotSkillLayoutStateModel.env == env,
                BotSkillLayoutStateModel.entity_id == bot.entity_id,
                BotSkillLayoutStateModel.bot_id == bot_id,
                BotSkillLayoutStateModel.phase
                == SkillLayoutPhase.POOL_INITIALIZING.value,
                BotSkillLayoutStateModel.migration_generation.is_(None),
                BotSkillLayoutStateModel.preparation_id.is_(None),
                BotSkillLayoutStateModel.data_plane_cutover_committed == 0,
            ).delete(synchronize_session=False)
            return True


__all__ = ["BotInitialLayoutRepositoryMixin"]
