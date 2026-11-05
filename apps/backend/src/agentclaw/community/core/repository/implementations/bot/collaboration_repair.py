"""Read current collaboration repair facts from the saved Bot row only.

Each call binds its explicit tenant and opens one short session, closes
it before returning, and touches nothing but the Bot table. It calls no
business mutator, writes no state, and does not read or change
caller configuration. Unreadable required values surface as
``RepairFactsError`` codes instead of becoming default payloads.
"""

from __future__ import annotations

import json

from injector import inject
from sqlalchemy.exc import SQLAlchemyError

from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.core.bot_management.collaboration_repair.profile import (
    avatar_from_record,
)
from agentclaw.community.core.repository.protocols.bot.collaboration_repair import (
    CollaborationRepairReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.kernel.collaboration_repair import (
    ProfileSnapshot,
    RepairFacts,
    RepairFactsError,
    RepairFactsErrorCode,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class SqlCollaborationRepairReadRepository(CollaborationRepairReadRepository):
    """Scoped, read-only SQL implementation of the repair facts contract."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def read_facts(self, key: SyncKey) -> RepairFacts:
        with avernet_tenant_scope(key.avernet_tenant):
            try:
                with self._db.orm_session() as session:
                    bot = (
                        session.query(BotModel)
                        .filter(
                            BotModel.id == key.bot_pk,
                            BotModel.avernet_tenant == key.avernet_tenant,
                            BotModel.env == key.env,
                            BotModel.is_delete == 0,
                        )
                        .one_or_none()
                    )
                    if bot is None:
                        raise RepairFactsError(
                            RepairFactsErrorCode.COLLABORATION_BOT_NOT_FOUND
                        )
                    return self._project(key, bot)
            except SQLAlchemyError as exc:
                raise RepairFactsError(
                    RepairFactsErrorCode.COLLABORATION_FACTS_READ_ERROR
                ) from exc

    def _project(self, key: SyncKey, bot: BotModel) -> RepairFacts:
        """Read the saved row into frozen facts, failing closed on bad values.

        Fields read from the row: ``bot_id``, ``entity_id`` (stable
        address), ``bot_name``/``bot_desc`` (profile text), ``ext``
        (avatar projection), ``owner_id`` (current Owner). The
        session is still open here; the frozen facts are returned only
        after it closes.
        """
        # Validate ext before reading the retained registration reference from it.
        avatar_url = self._avatar(bot)
        bcs_bot_id = self._bcs_bot_id(bot)
        return RepairFacts(
            key=key,
            profile=ProfileSnapshot(
                bot_id=bot.bot_id,
                bcs_bot_id=bcs_bot_id,
                # Same projection the daily BCN metadata path sends: a
                # missing saved name projects to the stable id, a missing
                # saved description to an explicit empty summary.
                name=bot.bot_name or bot.bot_id,
                summary=bot.bot_desc or "",
                avatar_url=avatar_url,
            ),
            owner_user_id=bot.owner_id,
        )

    @staticmethod
    def _bcs_bot_id(bot: BotModel) -> str:
        """Use the same established reference as registration and sync projectors."""
        for name, value in (("bot_id", bot.bot_id), ("entity_id", bot.entity_id)):
            if not isinstance(value, str) or not value or value != value.strip():
                raise RepairFactsError(
                    RepairFactsErrorCode.COLLABORATION_BOT_IDENTITY_UNRESOLVED
                )
        try:
            return bcn_bot_reference({
                "bot_id": bot.bot_id,
                "entity_id": bot.entity_id,
                "owner_id": bot.owner_id,
                "bot_type": bot.bot_type,
                # _project has already validated that ext is a JSON object.
                "ext": json.loads(bot.ext) if bot.ext is not None else {},
            })
        except ValueError as exc:
            raise RepairFactsError(
                RepairFactsErrorCode.COLLABORATION_BOT_IDENTITY_UNRESOLVED
            ) from exc

    @staticmethod
    def _avatar(bot: BotModel) -> str:
        """Project the saved avatar exactly; unreadable values raise.

        The record has no top-level avatar column, so the row's ``ext``
        (raw JSON string) is the only stored source; a corrupt or
        illegal value surfaces as a facts error instead of becoming a
        fabricated explicit clear.
        """
        try:
            return avatar_from_record({"ext": bot.ext})
        except ValueError as exc:
            raise RepairFactsError(
                RepairFactsErrorCode.COLLABORATION_AVATAR_UNREADABLE
            ) from exc
