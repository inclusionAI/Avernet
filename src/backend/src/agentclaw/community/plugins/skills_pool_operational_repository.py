"""Read-only ORM queries used by Skills Pool operational reporting."""

from sqlalchemy import case, func

from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutState


class SkillsPoolOperationalRepositoryMixin:
    """Keep reporting scans outside the migration state repository module."""

    _database: object

    def list_states(
        self,
        *,
        env: str,
        engine: str | None = None,
        batch_id: str | None = None,
    ) -> list[BotSkillLayoutState]:
        with self._database.transactional_orm_session() as session:
            query = session.query(BotSkillLayoutStateModel).filter(
                BotSkillLayoutStateModel.env == env
            )
            if engine is not None or batch_id is not None:
                query = query.filter(
                    func.json_valid(
                        BotSkillLayoutStateModel.rollout_evidence
                    )
                    == 1
                )
            dialect = session.get_bind().dialect.name
            if engine is not None:
                engine_value = case(
                    (
                        func.json_valid(
                            BotSkillLayoutStateModel.rollout_evidence
                        )
                        == 1,
                        func.json_extract(
                            BotSkillLayoutStateModel.rollout_evidence,
                            "$.engine_type",
                        ),
                    ),
                    else_=None,
                )
                if dialect != "sqlite":
                    engine_value = func.json_unquote(engine_value)
                query = query.filter(engine_value == engine)
            if batch_id is not None:
                batch_value = case(
                    (
                        func.json_valid(
                            BotSkillLayoutStateModel.rollout_evidence
                        )
                        == 1,
                        func.json_extract(
                            BotSkillLayoutStateModel.rollout_evidence,
                            "$.batch_id",
                        ),
                    ),
                    else_=None,
                )
                if dialect != "sqlite":
                    batch_value = func.json_unquote(batch_value)
                query = query.filter(batch_value == batch_id)
            rows = query.order_by(BotSkillLayoutStateModel.id.asc()).all()
            return [row.to_state() for row in rows]
