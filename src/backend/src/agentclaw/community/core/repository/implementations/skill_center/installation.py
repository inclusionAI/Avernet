"""SQLAlchemy persistence for the Bot Skill Installation desired-state fact."""

from __future__ import annotations

from injector import inject
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

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

    def install(
        self, *, env: str, owner_id: str, bot_id: str, skill_id: str | int
    ) -> bool:
        skill_id = int(skill_id)
        tenant = get_current_avernet_tenant()
        values = {
            "avernet_tenant": tenant,
            "env": env,
            "owner_id": owner_id,
            "bot_id": bot_id,
            "skill_id": skill_id,
        }
        try:
            # Do not infer insertion from MySQL/OceanBase ``rowcount``: with
            # CLIENT_FOUND_ROWS a no-op duplicate update can report one row.
            # A plain insert makes "changed" unambiguous on every dialect.
            with self._db.orm_session() as db:
                db.execute(insert(BotSkillInstallation).values(**values))
            return True
        except IntegrityError:
            # A concurrent winner is the only recoverable constraint outcome.
            # Re-read the exact unique identity in a fresh transaction; any
            # other integrity failure still propagates to the caller.
            with self._db.orm_session() as db:
                installed = db.execute(
                    select(BotSkillInstallation.id).where(
                        BotSkillInstallation.avernet_tenant == tenant,
                        BotSkillInstallation.env == env,
                        BotSkillInstallation.owner_id == owner_id,
                        BotSkillInstallation.bot_id == bot_id,
                        BotSkillInstallation.skill_id == skill_id,
                    )
                ).first()
            if installed is not None:
                return False
            raise

    def uninstall(
        self, *, env: str, owner_id: str, bot_id: str, skill_id: str | int
    ) -> bool:
        with self._db.orm_session() as db:
            count = (
                db.query(BotSkillInstallation)
                .filter(
                    BotSkillInstallation.avernet_tenant == get_current_avernet_tenant(),
                    BotSkillInstallation.env == env,
                    BotSkillInstallation.owner_id == owner_id,
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id == int(skill_id),
                )
                .delete(synchronize_session=False)
            )
            return count > 0

    def list_installed_skill_ids(
        self, *, env: str, owner_id: str, bot_id: str
    ) -> set[int]:
        with self._db.orm_session() as db:
            rows = (
                db.query(BotSkillInstallation.skill_id)
                .filter(
                    BotSkillInstallation.avernet_tenant == get_current_avernet_tenant(),
                    BotSkillInstallation.env == env,
                    BotSkillInstallation.owner_id == owner_id,
                    BotSkillInstallation.bot_id == bot_id,
                )
                .all()
            )
            return {int(row[0]) for row in rows}
