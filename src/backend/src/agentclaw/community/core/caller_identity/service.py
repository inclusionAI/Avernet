"""Owner-managed MCP identity configuration and Bot-level Caller decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.bot_collaborator.repository.protocol import (
    BotCollabLockRepositoryProtocol,
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.caller_identity.contracts import (
    CALLER_IDENTITY_CAPABILITY,
    CallerCallTypeInvalidError,
    CallerContext,
    CallerIamTokenContext,
    CallerIdentityNotFoundError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
    CallerIdentityStage,
    CallerLockEpochError,
    CallerMcpNotFoundError,
    CallerMcpSyncError,
    McpCallType,
    McpCallTypeUpdateResult,
)
from agentclaw.community.core.caller_identity.protocols import CallerMcpSyncProtocol
from agentclaw.community.core.caller_identity.repository import (
    CallerIdentityEngineChangedError,
    CallerIdentityLockMismatchError,
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.core.mcp.services.repositories import BotMCPProvider
from agentclaw.community.log import get_logger


logger = get_logger()


class CallerIdentityService:
    """Persist per-MCP modes and expose the aggregate Bot call type."""

    def __init__(
        self,
        *,
        bot_repository: BotRepository,
        collaborator_repository: CollaboratorRepositoryProtocol,
        lock_repository: BotCollabLockRepositoryProtocol,
        mcp_provider: BotMCPProvider,
        repository: CallerIdentityRepositoryProtocol,
        mcp_sync_service: CallerMcpSyncProtocol,
    ) -> None:
        self._bot_repository = bot_repository
        self._collaborator_repository = collaborator_repository
        self._lock_repository = lock_repository
        self._mcp_provider = mcp_provider
        self._repository = repository
        self._mcp_sync_service = mcp_sync_service

    async def update_mcp_call_type(
        self,
        *,
        bot_id: str,
        server_code: str,
        call_type: McpCallType,
        actor_id: str,
        lock_epoch: int,
    ) -> McpCallTypeUpdateResult:
        """Update draft state, then synchronously refresh Agent Principal."""
        normalized_call_type = self._parse_call_type(call_type)
        bot = self._bot_repository.get_by_id_and_owner(bot_id, actor_id)
        if bot is None or str(bot.get("owner_id") or "") != actor_id:
            raise CallerIdentityPermissionError
        if bot.get("bot_type") != "service" or bot.get("status") != "ACTIVE":
            raise CallerIdentityReadOnlyError

        engine_type = str(bot["active_engine"])
        active_mcps = self._mcp_provider.collect_bot_active_mcps(
            entity_id=str(bot["entity_id"]),
            bot_id=bot_id,
            user_id=actor_id,
            entity_type=str(bot.get("entity_type") or "staff"),
            engine_type=engine_type,
        )
        effective_mcps = {
            str(item["server_code"]): item
            for item in active_mcps
            if isinstance(item, Mapping) and item.get("server_code")
        }
        if server_code not in effective_mcps:
            raise CallerMcpNotFoundError

        lock_key = f"{bot_id}:{actor_id}"
        lock = self._lock_repository.get_by_key(lock_key)
        if lock is None or lock.holder_user_id != actor_id or lock.id != lock_epoch:
            raise CallerLockEpochError

        try:
            mutation = self._repository.replace_draft_call_type(
                bot_pk=int(bot["id"]),
                engine_type=engine_type,
                server_code=server_code,
                call_type=normalized_call_type,
                modifier_id=actor_id,
                effective_server_codes=set(effective_mcps),
                lock_key=lock_key,
                lock_holder_user_id=actor_id,
                lock_epoch=lock_epoch,
            )
        except CallerIdentityLockMismatchError as exc:
            raise CallerLockEpochError from exc
        except CallerIdentityEngineChangedError as exc:
            raise CallerIdentityReadOnlyError from exc
        except (TypeError, ValueError) as exc:
            raise CallerCallTypeInvalidError from exc

        try:
            identity_modes = self._repository.list_draft_call_types(
                int(bot["id"]),
                engine_type,
            )
            result = await self._mcp_sync_service.sync_mcp_identity_to_agent_principal(
                user_id=actor_id,
                entity_id=str(bot["entity_id"]),
                bot_id=bot_id,
                entity_type=str(bot.get("entity_type") or "staff"),
                engine_type=engine_type,
                active_mcps=list(effective_mcps.values()),
                identity_modes=identity_modes,
            )
        except Exception:
            result = {"success": False}
        if not isinstance(result, Mapping) or result.get("success") is not True:
            self._compensate_after_sync_failure(
                bot=bot,
                bot_id=bot_id,
                server_code=server_code,
                engine_type=engine_type,
                mutation=mutation,
                effective_server_codes=set(effective_mcps),
                actor_id=actor_id,
                lock_epoch=lock_epoch,
            )
            raise CallerMcpSyncError

        logger.info(
            "caller_mcp_call_type_updated bot_id=%s server_code=%s call_type=%s "
            "bot_call_type=%s",
            bot_id,
            server_code,
            normalized_call_type.value,
            mutation.bot_call_type.value,
        )
        return McpCallTypeUpdateResult(
            server_code=server_code,
            call_type=normalized_call_type,
            bot_call_type=mutation.bot_call_type,
        )

    def get_context(
        self,
        *,
        bot_id: str,
        actor_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
    ) -> CallerContext:
        """Return draft MCP details and the Bot aggregate for an authorized user."""
        normalized_stage = CallerIdentityStage(stage)
        bot, is_owner = self._authorize_read(bot_id, actor_id)
        mcp_call_types = dict(
            self._repository.list_draft_call_types(
                int(bot["id"]),
                str(bot["active_engine"]),
            )
        )
        return CallerContext(
            capability=CALLER_IDENTITY_CAPABILITY,
            stage=normalized_stage,
            publish_id=publish_id,
            bot_call_type=self._bot_call_type(bot),
            mcp_call_types=mcp_call_types,
            editable=(
                is_owner
                and normalized_stage is CallerIdentityStage.DRAFT
                and self._holds_lock(bot, actor_id)
            ),
        )

    def get_bot_call_type(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
    ) -> McpCallType:
        """Provide marketplace callers the aggregate without MCP enumeration."""
        del stage, publish_id
        return self._bot_call_type(self._get_bot(bot_id))

    def is_caller_bot(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
    ) -> bool:
        return self.get_bot_call_type(bot_id, stage, publish_id) is McpCallType.CALLER

    def get_iam_token_context(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
    ) -> CallerIamTokenContext:
        """Use only ac_bots.call_type to decide the IAM Caller branch."""
        bot = self._get_bot(bot_id)
        bot_call_type = self._bot_call_type(bot)
        should_exchange = (
            bot.get("bot_type") == "service"
            and bot.get("status") == "ACTIVE"
            and bot_call_type is McpCallType.CALLER
        )
        logger.info(
            "caller_iam_context_resolved bot_id=%s stage=%s caller=%s",
            bot_id,
            CallerIdentityStage(stage).value,
            should_exchange,
        )
        return CallerIamTokenContext(
            bot_id=bot_id,
            owner_id=str(bot.get("owner_id") or "") or None,
            stage=CallerIdentityStage(stage),
            publish_id=publish_id,
            bot_call_type=bot_call_type,
            should_exchange_caller_token=should_exchange,
        )

    def _compensate_after_sync_failure(
        self,
        *,
        bot: Mapping[str, Any],
        bot_id: str,
        server_code: str,
        engine_type: str,
        mutation: Any,
        effective_server_codes: set[str],
        actor_id: str,
        lock_epoch: int,
    ) -> None:
        try:
            compensation = self._repository.compensate_draft_call_type(
                bot_pk=int(bot["id"]),
                engine_type=engine_type,
                server_code=server_code,
                previous_explicit_call_type=mutation.previous_explicit_call_type,
                modifier_id=actor_id,
                effective_server_codes=effective_server_codes,
                expected_revision=mutation.revision,
                lock_key=f"{bot_id}:{actor_id}",
                lock_holder_user_id=actor_id,
                lock_epoch=lock_epoch,
            )
            logger.warning(
                "caller_agent_principal_sync_compensated bot_id=%s server_code=%s "
                "applied=%s revision=%s",
                bot_id,
                server_code,
                compensation.applied,
                compensation.revision,
            )
        except Exception as exc:
            logger.error(
                "caller_agent_principal_sync_compensation_failed bot_id=%s "
                "server_code=%s error_type=%s",
                bot_id,
                server_code,
                type(exc).__name__,
            )

    def _authorize_read(
        self,
        bot_id: str,
        actor_id: str,
    ) -> tuple[Mapping[str, Any], bool]:
        bot = self._get_bot(bot_id)
        is_owner = str(bot.get("owner_id") or "") == actor_id
        if is_owner:
            return bot, True
        collaborator = self._collaborator_repository.get_by_bot_and_user(
            int(bot["id"]),
            actor_id,
            str(bot["env"]),
        )
        if collaborator is None:
            raise CallerIdentityPermissionError
        return bot, False

    def _get_bot(self, bot_id: str) -> Mapping[str, Any]:
        bot = self._bot_repository.get_by_id(bot_id)
        if bot is None:
            raise CallerIdentityNotFoundError
        return bot

    def _holds_lock(self, bot: Mapping[str, Any], actor_id: str) -> bool:
        lock = self._lock_repository.get_by_key(f"{bot['bot_id']}:{bot['owner_id']}")
        return lock is not None and lock.holder_user_id == actor_id

    @staticmethod
    def _bot_call_type(bot: Mapping[str, Any]) -> McpCallType:
        try:
            return McpCallType.parse(bot.get("call_type"))
        except (TypeError, ValueError) as exc:
            raise CallerCallTypeInvalidError from exc

    @staticmethod
    def _parse_call_type(value: object) -> McpCallType:
        try:
            return McpCallType.parse(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise CallerCallTypeInvalidError from exc


__all__ = ["CallerIdentityService"]
