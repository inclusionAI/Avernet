"""Read current collaboration repair facts from the saved Bot row only.

Each call binds its explicit tenant and opens one short session, closes
it before returning, and touches nothing but the Bot table. It calls no
business mutator, writes no state, and never bumps
``caller_config_revision``. Unreadable required values surface as
``RepairFactsError`` codes instead of becoming default payloads.
"""

from __future__ import annotations

from injector import inject
from sqlalchemy.exc import SQLAlchemyError

from agentclaw.community.core.bot_management.collaboration_repair.profile import (
    avatar_from_record,
)
from agentclaw.community.core.caller_identity.models import McpCallType
from agentclaw.community.core.repository.protocols.bot.collaboration_repair import (
    CollaborationRepairReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.kernel.collaboration_repair import (
    CallerSnapshot,
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
        (avatar projection), ``owner_id`` (current Owner), ``call_type``
        and ``caller_config_revision`` (caller applicability). The
        session is still open here; the frozen facts are returned only
        after it closes.
        """
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
                avatar_url=self._avatar(bot),
            ),
            owner_user_id=bot.owner_id,
            caller=self._caller(bot, bcs_bot_id),
        )

    @staticmethod
    def _bcs_bot_id(bot: BotModel) -> str:
        """Compose the immutable BCS address, refusing owner-derived parts.

        The address is always ``<bot_id>:<entity_id>``; a missing,
        blank or padded saved value raises instead of being guessed,
        and the current Owner is never substituted for the entity.
        """
        for name, value in (("bot_id", bot.bot_id), ("entity_id", bot.entity_id)):
            if not isinstance(value, str) or not value or value != value.strip():
                raise RepairFactsError(
                    RepairFactsErrorCode.COLLABORATION_BOT_IDENTITY_UNRESOLVED
                )
        return f"{bot.bot_id}:{bot.entity_id}"

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

    @staticmethod
    def _caller(bot: BotModel, bcs_bot_id: str) -> CallerSnapshot | None:
        """Project caller applicability from the saved revision.

        A saved ``caller_config_revision`` of 0 means no caller config
        has ever been established (nothing applicable to repair), which
        is the only ``None`` case. An established config is always
        reported, enabled or disabled: ``enabled`` follows the saved
        aggregate call type and is never used as the applicability
        marker, the saved revision travels unchanged, and an unknown
        saved call type raises instead of being parsed into a default.
        """
        try:
            parsed = McpCallType.parse(bot.call_type)
        except (TypeError, ValueError) as exc:
            raise RepairFactsError(
                RepairFactsErrorCode.COLLABORATION_CALL_TYPE_INVALID
            ) from exc
        revision = int(bot.caller_config_revision)
        if revision <= 0:
            return None
        return CallerSnapshot(
            bcs_bot_id=bcs_bot_id,
            enabled=parsed is McpCallType.CALLER,
            source_revision=revision,
        )
