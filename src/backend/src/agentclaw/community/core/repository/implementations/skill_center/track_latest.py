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
    TrackLatestCandidate,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class TrackLatestRepository(TrackLatestRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def list_candidates(
        self, *, env: str, skill_id: int
    ) -> tuple[TrackLatestCandidate, ...]:
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
            ordinary_pairs = {
                (str(row.user_id), str(row.bolt_id))
                for row in set_rows
                if not row.is_default and row.user_id and row.bolt_id
            }
            active_ordinary_pairs = {
                (str(row.user_id), str(row.bolt_id))
                for row in set_rows
                if (
                    not row.is_default
                    and row.is_active
                    and row.user_id
                    and row.bolt_id
                )
            }
            default_engines = {
                row.engine_type for row in set_rows if bool(row.is_default)
            }

            installed = (
                session.query(BotSkillInstallation, BotModel)
                .join(
                    BotModel,
                    (BotModel.bot_id == BotSkillInstallation.bot_id)
                    & (BotModel.owner_id == BotSkillInstallation.owner_id)
                    & (BotModel.env == BotSkillInstallation.env)
                    & (
                        BotModel.avernet_tenant
                        == BotSkillInstallation.avernet_tenant
                    ),
                )
                .filter(
                    BotSkillInstallation.avernet_tenant == tenant,
                    BotSkillInstallation.env == env,
                    BotSkillInstallation.skill_id == skill_id,
                    BotModel.is_delete == 0,
                )
                .all()
            )
            candidates: set[tuple[str, str]] = set()
            for _installation, bot in installed:
                pair = (str(bot.owner_id), str(bot.bot_id))
                if pair in active_ordinary_pairs:
                    candidates.add(pair)
                    continue
                default_reaches = (
                    None in default_engines or bot.active_engine in default_engines
                )
                if pair not in ordinary_pairs and not default_reaches:
                    candidates.add(pair)

            if active_ordinary_pairs:
                bot_ids = {bot_id for _owner_id, bot_id in active_ordinary_pairs}
                bots = (
                    session.query(BotModel)
                    .filter(
                        BotModel.avernet_tenant == tenant,
                        BotModel.env == env,
                        BotModel.is_delete == 0,
                        BotModel.bot_id.in_(bot_ids),
                    )
                    .all()
                )
                live_pairs = {
                    (str(bot.owner_id), str(bot.bot_id)) for bot in bots
                }
                candidates.update(active_ordinary_pairs & live_pairs)

            return tuple(
                TrackLatestCandidate(owner_id=owner_id, bot_id=bot_id)
                for owner_id, bot_id in sorted(candidates)
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
