"""Owner-managed MCP identity configuration and Bot-level Caller decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import BotCollabLockRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.caller_identity.contracts import (
    CALLER_IDENTITY_CAPABILITY,
    CallerCallTypeInvalidError,
    CallerContext,
    CallerIamTokenContext,
    CallerIdentityAmbiguousError,
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
from agentclaw.community.core.caller_identity.credential import (
    CALLER_CHAT_TASK,
    AuthContext,
)
from agentclaw.community.core.caller_identity.protocols import (
    CallerMcpSyncProtocol,
    CallerRuntimeUpdaterProtocol,
    CallerTokenProviderProtocol,
)
from agentclaw.community.core.repository.protocols.identity import CallerIdentityRepositoryProtocol
from agentclaw.community.core.caller_identity.contracts import CallerIdentityEngineChangedError, CallerIdentityLockMismatchError
from agentclaw.community.core.mcp.services.repositories import BotMCPProvider
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


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
        lock_epoch: int | None = None,
        entity_id: str | None = None,
    ) -> McpCallTypeUpdateResult:
        """Update draft state, then synchronously refresh Agent Principal."""
        normalized_call_type = self._parse_call_type(call_type)
        if entity_id is not None:
            bot = self._bot_repository.get_by_id_and_entity(bot_id, entity_id)
        else:
            try:
                # COSEC: do not select an arbitrary duplicate Bot when callers
                # omit entity_id; the mutation must fail closed.
                bot = self._bot_repository.get_unique_by_id(bot_id)
            except BotLookupAmbiguousError as exc:
                logger.warning(
                    "caller_mcp_call_type_update_rejected_ambiguous_bot bot_id=%s",
                    bot_id,
                )
                raise CallerIdentityAmbiguousError from exc
        # COSEC: an entity-scoped lookup does not authorize the request; the
        # authenticated actor must still be the Bot owner.
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
        if lock_epoch is None:
            if lock is not None:
                logger.warning(
                    "caller_mcp_call_type_update_rejected_missing_lock_epoch "
                    "bot_id=%s",
                    bot_id,
                )
                raise CallerLockEpochError
        elif lock is None or lock.holder_user_id != actor_id or lock.id != lock_epoch:
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
            "bot_call_type=%s lock_epoch_supplied=%s",
            bot_id,
            server_code,
            normalized_call_type.value,
            mutation.bot_call_type.value,
            lock_epoch is not None,
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
        entity_id: str | None = None,
    ) -> CallerContext:
        """Return draft MCP details and the Bot aggregate for an authorized user."""
        normalized_stage = CallerIdentityStage(stage)
        bot, is_owner = self._authorize_read(bot_id, actor_id, entity_id)
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
                and self._can_edit_draft(bot, actor_id)
            ),
        )

    def get_bot_call_type(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
        entity_id: str | None = None,
    ) -> McpCallType:
        """Provide marketplace callers the aggregate without MCP enumeration."""
        del stage, publish_id
        return self._bot_call_type(self._get_bot(bot_id, entity_id))

    def is_caller_bot(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
        entity_id: str | None = None,
    ) -> bool:
        return (
            self.get_bot_call_type(bot_id, stage, publish_id, entity_id)
            is McpCallType.CALLER
        )

    def get_iam_token_context(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
        entity_id: str | None = None,
        is_test_exchange: bool = False,
    ) -> CallerIamTokenContext:
        """Resolve whether the IAM request should execute the Caller branch."""
        if is_test_exchange and get_current_env() == "prod":
            logger.warning(
                "caller_test_exchange_rejected bot_id=%s reason=production_environment",
                bot_id,
            )
            raise CallerIdentityPermissionError
        bot = self._get_bot(bot_id, entity_id)
        resolved_stage = CallerIdentityStage(stage)
        # COSEC: temporary browser testing only bypasses the Bot type and
        # aggregate call-type lookup/gates. Exact Bot resolution, ACTIVE state and
        # the downstream IAM/Passport/runtime validations remain mandatory.
        bot_call_type = (
            McpCallType.OWNER if is_test_exchange else self._bot_call_type(bot)
        )
        should_exchange = (
            bot.get("status") == "ACTIVE"
            and (
                is_test_exchange
                or (
                    bot.get("bot_type") == "service"
                    and bot_call_type is McpCallType.CALLER
                )
            )
        )
        binding_id = (
            bot.get("binding_id")
            if resolved_stage is CallerIdentityStage.DRAFT
            else None
        )
        if (
            not isinstance(binding_id, int)
            or isinstance(binding_id, bool)
            or binding_id <= 0
        ):
            binding_id = None
        logger.info(
            "caller_iam_context_resolved bot_id=%s stage=%s caller=%s "
            "test_exchange=%s entity_scoped=%s draft_binding_available=%s",
            bot_id,
            resolved_stage.value,
            should_exchange,
            is_test_exchange,
            entity_id is not None,
            binding_id is not None,
        )
        return CallerIamTokenContext(
            bot_id=bot_id,
            owner_id=str(bot.get("owner_id") or "") or None,
            stage=resolved_stage,
            publish_id=publish_id,
            bot_call_type=bot_call_type,
            should_exchange_caller_token=should_exchange,
            binding_id=binding_id,
        )

    def authorize_iam_token_exchange(
        self,
        *,
        caller_user_id: str,
        owner_user_id: str,
        is_test_exchange: bool,
    ) -> None:
        """Restrict the temporary test-exchange path to the Bot owner."""
        if is_test_exchange and caller_user_id != owner_user_id:
            logger.warning(
                "caller_test_exchange_rejected reason=not_owner",
            )
            raise CallerIdentityPermissionError

    def exchange_caller_identity(
        self,
        *,
        iam_token: str,
        caller_user_id: str,
        bot_id: str,
        owner_user_id: str,
        token_provider: CallerTokenProviderProtocol,
        runtime_updater: CallerRuntimeUpdaterProtocol,
        stage: str,
        publish_id: int | None,
        entity_id: str | None = None,
        binding_id: int | None = None,
        is_test_exchange: bool = False,
    ) -> None:
        """Exchange and install the Caller credential for one chat request."""
        caller_token = token_provider.exchange(
            auth_context=AuthContext(user_id=caller_user_id),
            iam_token=iam_token,
            bot_id=bot_id,
            owner_user_id=owner_user_id,
            task_metadata=CALLER_CHAT_TASK,
        )
        runtime_update_kwargs: dict[str, Any] = {
            "bot_id": bot_id,
            "owner_user_id": owner_user_id,
            "caller_user_id": caller_user_id,
            "caller_token": caller_token,
            "stage": stage,
            "publish_id": publish_id,
            "entity_id": entity_id,
        }
        if isinstance(binding_id, int) and not isinstance(binding_id, bool) and binding_id > 0:
            runtime_update_kwargs["binding_id"] = binding_id
        if is_test_exchange:
            # The HTTP adapter restricts this temporary path to a non-production
            # Bot owner. BaaS needs the marker only to accept a personal Bot for
            # that controlled end-to-end test.
            runtime_update_kwargs["is_test_exchange"] = True
        logger.info(
            "caller_runtime_update_requested bot_id=%s stage=%s test_exchange=%s",
            bot_id,
            stage,
            is_test_exchange,
        )
        runtime_updater.update_caller_identity(
            **runtime_update_kwargs,
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
        lock_epoch: int | None,
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
        entity_id: str | None,
    ) -> tuple[Mapping[str, Any], bool]:
        bot = self._get_bot(bot_id, entity_id)
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

    def _get_bot(
        self,
        bot_id: str,
        entity_id: str | None = None,
    ) -> Mapping[str, Any]:
        if entity_id is not None:
            bot = self._bot_repository.get_by_id_and_entity(bot_id, entity_id)
        else:
            try:
                # COSEC: do not select an arbitrary duplicate Bot when callers
                # omit entity_id; the caller-specific lookup fails closed.
                bot = self._bot_repository.get_unique_by_id(bot_id)
            except BotLookupAmbiguousError as exc:
                raise CallerIdentityAmbiguousError from exc
        if bot is None:
            raise CallerIdentityNotFoundError
        return bot

    def _holds_lock(self, bot: Mapping[str, Any], actor_id: str) -> bool:
        lock = self._lock_repository.get_by_key(f"{bot['bot_id']}:{bot['owner_id']}")
        return lock is not None and lock.holder_user_id == actor_id

    def _can_edit_draft(self, bot: Mapping[str, Any], actor_id: str) -> bool:
        """Permit an owner when no collaboration lock has been created yet."""
        lock = self._lock_repository.get_by_key(f"{bot['bot_id']}:{bot['owner_id']}")
        return lock is None or lock.holder_user_id == actor_id

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
