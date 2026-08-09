"""Transactional persistence for service-Bot Caller configuration."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.bot_collaborator.models import BotCollabLockModel
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityIrreversibleError,
    DraftCallTypeCompensationResult,
    DraftCallTypeMutationResult,
)
from agentclaw.community.core.caller_identity.models import (
    BotMcpCallConfigModel,
    McpCallType,
)
from agentclaw.community.core.caller_identity.contracts import CallerIdentityEngineChangedError, CallerIdentityLockMismatchError
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class CallerIdentityRepository:
    """Keep MCP overrides and the Bot aggregate in one database transaction."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def replace_draft_call_type(
        self,
        *,
        bot_pk: int,
        engine_type: str,
        server_code: str,
        call_type: McpCallType,
        modifier_id: str,
        effective_server_codes: set[str],
        lock_key: str,
        lock_holder_user_id: str,
        lock_epoch: int | None,
    ) -> DraftCallTypeMutationResult:
        normalized_call_type = McpCallType.parse(call_type)
        env = get_current_env()
        with self._db.transactional_orm_session() as session:
            if lock_epoch is not None:
                # COSEC: verify the exact lock inside the write transaction so a
                # released or stolen lock cannot authorize a stale mutation.
                lock = (
                    session.query(BotCollabLockModel)
                    .filter(
                        BotCollabLockModel.lock_key == lock_key,
                        BotCollabLockModel.holder_user_id == lock_holder_user_id,
                        BotCollabLockModel.id == lock_epoch,
                        BotCollabLockModel.env == env,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if lock is None:
                    raise CallerIdentityLockMismatchError
            else:
                # COSEC: repeat the unlocked-path check in the write
                # transaction. A lock created after the service check must not
                # be bypassed by an omitted epoch.
                lock = (
                    session.query(BotCollabLockModel)
                    .filter(
                        BotCollabLockModel.lock_key == lock_key,
                        BotCollabLockModel.env == env,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if lock is not None:
                    raise CallerIdentityLockMismatchError
            bot = (
                session.query(BotModel)
                .filter(BotModel.id == bot_pk, BotModel.env == env)
                .with_for_update()
                .one()
            )
            if bot.active_engine != engine_type:
                raise CallerIdentityEngineChangedError
            row = (
                session.query(BotMcpCallConfigModel)
                .filter(
                    BotMcpCallConfigModel.bot_pk == bot_pk,
                    BotMcpCallConfigModel.server_code == server_code,
                    BotMcpCallConfigModel.engine_type == engine_type,
                    BotMcpCallConfigModel.env == env,
                )
                .one_or_none()
            )
            previous = McpCallType.parse(row.call_type) if row is not None else None
            if normalized_call_type is McpCallType.OWNER:
                if row is not None:
                    session.delete(row)
            elif row is None:
                session.add(
                    BotMcpCallConfigModel(
                        bot_pk=bot_pk,
                        server_code=server_code,
                        engine_type=engine_type,
                        call_type=normalized_call_type.value,
                        modifier_id=modifier_id,
                        env=env,
                    )
                )
            else:
                row.call_type = normalized_call_type.value
                row.modifier_id = modifier_id

            session.flush()
            aggregate = self._aggregate(
                session,
                bot_pk=bot_pk,
                engine_type=engine_type,
                effective_server_codes=effective_server_codes,
                env=env,
            )
            # COSEC: the aggregate transition is checked while holding the Bot
            # row lock so concurrent MCP edits cannot downgrade a Caller Bot.
            if bot.call_type == McpCallType.CALLER.value and aggregate is McpCallType.OWNER:
                logger.warning(
                    "caller_mcp_call_type_update_rejected_irreversible "
                    "bot_pk=%s server_code=%s previous_bot_call_type=%s "
                    "next_bot_call_type=%s",
                    bot_pk,
                    server_code,
                    McpCallType.CALLER.value,
                    aggregate.value,
                )
                raise CallerIdentityIrreversibleError
            revision = int(bot.caller_config_revision or 0) + 1
            bot.call_type = aggregate.value
            bot.caller_config_revision = revision
            bot.modifier_id = modifier_id

        logger.info(
            "caller_mcp_call_type_updated bot_pk=%s server_code=%s call_type=%s "
            "bot_call_type=%s revision=%s",
            bot_pk,
            server_code,
            normalized_call_type.value,
            aggregate.value,
            revision,
        )
        return DraftCallTypeMutationResult(
            previous_explicit_call_type=previous,
            bot_call_type=aggregate,
            revision=revision,
        )

    def compensate_draft_call_type(
        self,
        *,
        bot_pk: int,
        engine_type: str,
        server_code: str,
        previous_explicit_call_type: McpCallType | None,
        modifier_id: str,
        effective_server_codes: set[str],
        expected_revision: int,
        lock_key: str,
        lock_holder_user_id: str,
        lock_epoch: int | None,
    ) -> DraftCallTypeCompensationResult:
        env = get_current_env()
        with self._db.transactional_orm_session() as session:
            if lock_epoch is not None:
                lock = (
                    session.query(BotCollabLockModel)
                    .filter(
                        BotCollabLockModel.lock_key == lock_key,
                        BotCollabLockModel.holder_user_id == lock_holder_user_id,
                        BotCollabLockModel.id == lock_epoch,
                        BotCollabLockModel.env == env,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if lock is None:
                    raise CallerIdentityLockMismatchError
            else:
                # COSEC: do not roll back an unlocked mutation after another
                # editor has established a collaboration lock.
                lock = (
                    session.query(BotCollabLockModel)
                    .filter(
                        BotCollabLockModel.lock_key == lock_key,
                        BotCollabLockModel.env == env,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if lock is not None:
                    raise CallerIdentityLockMismatchError
            bot = (
                session.query(BotModel)
                .filter(BotModel.id == bot_pk, BotModel.env == env)
                .with_for_update()
                .one()
            )
            if bot.active_engine != engine_type:
                raise CallerIdentityEngineChangedError
            if int(bot.caller_config_revision or 0) != expected_revision:
                return DraftCallTypeCompensationResult(
                    applied=False,
                    bot_call_type=McpCallType.parse(bot.call_type),
                    revision=int(bot.caller_config_revision or 0),
                )
            row = (
                session.query(BotMcpCallConfigModel)
                .filter(
                    BotMcpCallConfigModel.bot_pk == bot_pk,
                    BotMcpCallConfigModel.server_code == server_code,
                    BotMcpCallConfigModel.engine_type == engine_type,
                    BotMcpCallConfigModel.env == env,
                )
                .one_or_none()
            )
            if previous_explicit_call_type is None:
                if row is not None:
                    session.delete(row)
            elif row is None:
                session.add(
                    BotMcpCallConfigModel(
                        bot_pk=bot_pk,
                        server_code=server_code,
                        engine_type=engine_type,
                        call_type=previous_explicit_call_type.value,
                        modifier_id=modifier_id,
                        env=env,
                    )
                )
            else:
                row.call_type = previous_explicit_call_type.value
                row.modifier_id = modifier_id
            session.flush()
            aggregate = self._aggregate(
                session,
                bot_pk=bot_pk,
                engine_type=engine_type,
                effective_server_codes=effective_server_codes,
                env=env,
            )
            revision = expected_revision + 1
            bot.call_type = aggregate.value
            bot.caller_config_revision = revision
            bot.modifier_id = modifier_id
        logger.warning(
            "caller_mcp_call_type_compensated bot_pk=%s server_code=%s revision=%s",
            bot_pk,
            server_code,
            revision,
        )
        return DraftCallTypeCompensationResult(
            applied=True,
            bot_call_type=aggregate,
            revision=revision,
        )

    def list_draft_call_types(
        self,
        bot_pk: int,
        engine_type: str,
    ) -> dict[str, McpCallType]:
        env = get_current_env()
        with self._db.orm_session() as session:
            rows = (
                session.query(BotMcpCallConfigModel)
                .filter(
                    BotMcpCallConfigModel.bot_pk == bot_pk,
                    BotMcpCallConfigModel.engine_type == engine_type,
                    BotMcpCallConfigModel.env == env,
                )
                .order_by(BotMcpCallConfigModel.server_code.asc())
                .all()
            )
            return {row.server_code: McpCallType.parse(row.call_type) for row in rows}

    @staticmethod
    def _aggregate(
        session,
        *,
        bot_pk: int,
        engine_type: str,
        effective_server_codes: set[str],
        env: str,
    ) -> McpCallType:
        if not effective_server_codes:
            return McpCallType.OWNER
        rows = (
            session.query(BotMcpCallConfigModel.call_type)
            .filter(
                BotMcpCallConfigModel.bot_pk == bot_pk,
                BotMcpCallConfigModel.engine_type == engine_type,
                BotMcpCallConfigModel.env == env,
                BotMcpCallConfigModel.server_code.in_(effective_server_codes),
            )
            .all()
        )
        return (
            McpCallType.CALLER
            if any(
                McpCallType.parse(row.call_type) is McpCallType.CALLER for row in rows
            )
            else McpCallType.OWNER
        )
