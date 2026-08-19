"""SQLAlchemy persistence for the Bot Skill Installation desired-state fact."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.models.skill import BotSkillInstallation
from agentclaw.community.core.repository.protocols.skill_installation import (
    SkillInstallationRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class SkillInstallationRepository(SkillInstallationRepositoryProtocol):
    """Active-only Installation repository, scoped by tenant and environment."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def install(self, *, env: str, bot_id: str, skill_id: str | int) -> bool:
        skill_id = int(skill_id)
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as db:
            table = BotSkillInstallation.__table__
            values = {
                "avernet_tenant": tenant,
                "env": env,
                "bot_id": bot_id,
                "skill_id": skill_id,
            }
            if db.get_bind().dialect.name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert

                result = db.execute(
                    insert(table)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[
                            "avernet_tenant", "env", "bot_id", "skill_id"
                        ]
                    )
                )
            else:
                from sqlalchemy.dialects.mysql import insert

                result = db.execute(
                    insert(table)
                    .values(**values)
                    .on_duplicate_key_update(skill_id=table.c.skill_id)
                )
            return result.rowcount == 1

    def uninstall(self, *, env: str, bot_id: str, skill_id: str | int) -> bool:
        with self._db.orm_session() as db:
            count = (
                db.query(BotSkillInstallation)
                .filter(
                    BotSkillInstallation.avernet_tenant == get_current_avernet_tenant(),
                    BotSkillInstallation.env == env,
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id == int(skill_id),
                )
                .delete(synchronize_session=False)
            )
            return count > 0

    def list_installed_skill_ids(self, *, env: str, bot_id: str) -> set[int]:
        with self._db.orm_session() as db:
            rows = (
                db.query(BotSkillInstallation.skill_id)
                .filter(
                    BotSkillInstallation.avernet_tenant == get_current_avernet_tenant(),
                    BotSkillInstallation.env == env,
                    BotSkillInstallation.bot_id == bot_id,
                )
                .all()
            )
            return {int(row[0]) for row in rows}
