"""ORM persistence reads for Track Latest candidates and published Versions."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.repository.protocols.track_latest import TrackLatestRepositoryProtocol
from agentclaw.community.core.repository.track_latest_types import (
    PublishedTrackLatestVersion,
    TrackLatestBotFact,
    TrackLatestCandidateFacts,
    TrackLatestInstallationFact,
    TrackLatestSkillSetFact,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class TrackLatestRepository(TrackLatestRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def list_candidate_facts(
        self, *, env: str, skill_id: int
    ) -> TrackLatestCandidateFacts:
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            set_rows = (
                session.query(SkillSet)
                .join(SkillSetSkill, SkillSetSkill.skill_set_id == SkillSet.id)
                .filter(
                    SkillSet.avernet_tenant == tenant,
                    SkillSetSkill.avernet_tenant == tenant,
                    SkillSet.env == env,
                    SkillSetSkill.env == env,
                    SkillSetSkill.skill_id == skill_id,
                )
                .all()
            )
            installation_rows = (
                session.query(BotSkillInstallation)
                .filter(
                    BotSkillInstallation.avernet_tenant == tenant,
                    BotSkillInstallation.env == env,
                    BotSkillInstallation.skill_id == skill_id,
                )
                .all()
            )
            related_bot_ids = {
                str(row.bolt_id) for row in set_rows if row.bolt_id
            }
            related_bot_ids.update(
                str(row.bot_id) for row in installation_rows
            )
            bot_rows = ()
            if related_bot_ids:
                bot_rows = tuple(
                    session.query(BotModel)
                    .filter(
                        BotModel.avernet_tenant == tenant,
                        BotModel.env == env,
                        BotModel.bot_id.in_(related_bot_ids),
                    )
                    .all()
                )

            return TrackLatestCandidateFacts(
                installations=tuple(
                    TrackLatestInstallationFact(
                        owner_id=str(row.owner_id), bot_id=str(row.bot_id)
                    )
                    for row in installation_rows
                ),
                skill_sets=tuple(
                    TrackLatestSkillSetFact(
                        owner_id=str(row.user_id) if row.user_id else None,
                        bot_id=str(row.bolt_id) if row.bolt_id else None,
                        engine_type=(
                            str(row.engine_type) if row.engine_type else None
                        ),
                        is_default=bool(row.is_default),
                        is_active=bool(row.is_active),
                    )
                    for row in set_rows
                ),
                bots=tuple(
                    TrackLatestBotFact(
                        owner_id=str(row.owner_id),
                        bot_id=str(row.bot_id),
                        active_engine=str(row.active_engine),
                        is_deleted=bool(row.is_delete),
                    )
                    for row in bot_rows
                ),
            )

    def list_published_versions(
        self, *, env: str, skill_id: int
    ) -> tuple[PublishedTrackLatestVersion, ...]:
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            rows = (
                session.query(SkillVersion)
                .filter(
                    SkillVersion.avernet_tenant == tenant,
                    SkillVersion.env == env,
                    SkillVersion.skill_id == skill_id,
                    SkillVersion.status == "PUBLISHED",
                )
                .order_by(SkillVersion.version_ordinal.desc())
                .all()
            )
            return tuple(
                PublishedTrackLatestVersion(
                    skill_version_id=int(version.id),
                    metadata_json=version.metadata_json,
                )
                for version in rows
            )


__all__ = ["TrackLatestRepository"]
