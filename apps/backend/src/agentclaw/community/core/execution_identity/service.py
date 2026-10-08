"""Application service for persistent passport execution identity changes."""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.execution_identity.contracts import (
    ExecutionIdentityBinding,
    ExecutionIdentityNotFoundError,
    ExecutionIdentityOperationNotAllowedError,
    ExecutionIdentityType,
)
from agentclaw.community.core.execution_identity.protocols import (
    ExecutionIdentityServiceProtocol,
    RuntimePassportTokenUpdaterProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.identity import (
    ExecutionIdentityRepositoryProtocol,
)
from agentclaw.community.plugin_api.passport import PassportPlugin


def _public(binding: ExecutionIdentityBinding) -> dict[str, Any]:
    return {
        "binding_id": binding.id,
        "bot_pk": binding.bot_pk,
        "execution_workno": binding.execution_workno,
        "identity_type": binding.identity_type.value,
        "status": binding.status.value,
        "authorization_id": binding.authorization_id,
        "credential_id": binding.credential_id,
        "agent_id": binding.agent_id,
        "credential_status": binding.credential_status,
    }


class ExecutionIdentityService(ExecutionIdentityServiceProtocol):
    @inject
    def __init__(
        self,
        bot_repository: BotRepository,
        binding_repository: ExecutionIdentityRepositoryProtocol,
        passport_plugin: PassportPlugin,
        runtime_updater: RuntimePassportTokenUpdaterProtocol,
    ) -> None:
        self._bots = bot_repository
        self._bindings = binding_repository
        self._passport = passport_plugin
        self._runtime = runtime_updater

    def resolve_execution_workno(self, *, bot_pk: int, owner_id: str) -> str:
        """Return the activated executor, falling back to legacy owner identity."""
        active = self._bindings.get_active(bot_pk=bot_pk)
        return active.execution_workno if active is not None else owner_id

    def change_execution_identity(
        self,
        *,
        bot_id: str,
        owner_id: str,
        action: str,
        modifier_id: str,
        execution_workno: str | None = None,
        identity_type: str | None = None,
    ) -> dict[str, Any]:
        bot = self._bots.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        if bot.get("bot_type") != "service":
            raise ExecutionIdentityOperationNotAllowedError(
                "Only service Bot execution identity can be changed"
            )
        entity_id = str(bot.get("entity_id") or "").strip()
        if not entity_id:
            raise ExecutionIdentityOperationNotAllowedError(
                "Bot entity_id is required for passport addressing"
            )
        bot_pk = int(bot["id"])

        if action == "reissue":
            return self._reissue(
                bot=bot,
                bot_pk=bot_pk,
                entity_id=entity_id,
                owner_id=owner_id,
                execution_workno=execution_workno,
                identity_type=identity_type,
                modifier_id=modifier_id,
            )
        if action == "activate":
            return self._activate(
                bot_id=bot_id,
                bot_pk=bot_pk,
                entity_id=entity_id,
                owner_id=owner_id,
                modifier_id=modifier_id,
            )
        raise ExecutionIdentityOperationNotAllowedError(f"Unsupported action: {action}")

    def _reissue(
        self,
        *,
        bot: dict[str, Any],
        bot_pk: int,
        entity_id: str,
        owner_id: str,
        execution_workno: str | None,
        identity_type: str | None,
        modifier_id: str,
    ) -> dict[str, Any]:
        workno = (execution_workno or "").strip()
        if not workno or identity_type is None:
            raise ExecutionIdentityOperationNotAllowedError(
                "reissue requires execution_workno and identity_type"
            )
        try:
            kind = ExecutionIdentityType(identity_type)
        except ValueError as exc:
            raise ExecutionIdentityOperationNotAllowedError(
                f"Unsupported identity_type: {identity_type}"
            ) from exc

        pending = self._bindings.begin_pending(
            bot_pk=bot_pk,
            execution_workno=workno,
            identity_type=kind,
            modifier_id=modifier_id,
        )
        result = self._passport.reissue_agent_credentials(
            bot_id=str(bot["bot_id"]),
            owner_workno=owner_id,
            entity_id=entity_id,
            execution_workno=workno,
            bot_name=bot.get("bot_name"),
            bot_desc=bot.get("bot_desc"),
            engine_type=bot.get("active_engine"),
        )
        if not result:
            # A transport timeout can be an unknown remote outcome. Keep the
            # durable transition PENDING so activate can reconcile it later.
            raise ExecutionIdentityOperationNotAllowedError(
                "passport reissue returned no result; transition remains PENDING"
            )
        pending = self._bindings.record_credential_result(
            binding_id=pending.id,
            authorization_id=result.get("authorization_id"),
            credential_id=result.get("credential_id"),
            agent_id=result.get("agent_id"),
            credential_status=result.get("status"),
            modifier_id=modifier_id,
        )
        if result.get("token"):
            return self._activate(
                bot_id=str(bot["bot_id"]),
                bot_pk=bot_pk,
                entity_id=entity_id,
                owner_id=owner_id,
                modifier_id=modifier_id,
            )
        response = _public(pending)
        response.update({"owner_id": owner_id, "entity_id": entity_id})
        response["redirect_url"] = result.get("redirect_url")
        response["iframe_url"] = result.get("iframe_url")
        response["token_injected"] = False
        return response

    def _activate(
        self,
        *,
        bot_id: str,
        bot_pk: int,
        entity_id: str,
        owner_id: str,
        modifier_id: str,
    ) -> dict[str, Any]:
        pending = self._bindings.get_pending(bot_pk=bot_pk)
        if pending is None:
            raise ExecutionIdentityNotFoundError("No pending execution identity change")
        auth = self._passport.query_auth_status(
            bot_id, owner_id, entity_id=entity_id
        ) or {}
        token = auth.get("token")
        if not token:
            response = _public(pending)
            response.update(
                {
                    "owner_id": owner_id,
                    "entity_id": entity_id,
                    "credential_status": auth.get("status"),
                    "token_injected": False,
                }
            )
            return response
        passport = self._passport.query_agent_passport(
            bot_id, owner_id, entity_id=entity_id
        ) or {}
        if passport.get("execution_workno") != pending.execution_workno:
            raise ExecutionIdentityOperationNotAllowedError(
                "Latest passport execution_workno does not match pending binding"
            )
        runtime_result = self._runtime.hot_update_passport_token_to_device(
            bot_id=bot_id, user_id=owner_id, token=str(token)
        )
        active = self._bindings.activate_pending(
            binding_id=pending.id, modifier_id=modifier_id
        )
        response = _public(active)
        response.update(
            {
                "owner_id": owner_id,
                "entity_id": entity_id,
                "token_injected": True,
                "runtime": runtime_result,
            }
        )
        return response


__all__ = ["ExecutionIdentityService"]
