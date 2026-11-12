"""Validate the session context required by TeClaw Caller updates."""

from __future__ import annotations

from agentclaw.community.core.caller_identity.credential import (
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CALLER_TARGET_NOT_FOUND,
    CallerCredentialError,
)
from agentclaw.community.core.caller_identity.contracts import CallerIdentityPermissionError
from agentclaw.community.core.repository.protocols.chat import ExpertChatRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest, RuntimeBindingTarget
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.log import get_logger

logger = get_logger()


class CallerSessionValidator:
    def __init__(
        self,
        *,
        binding_repository: DeviceBindingRepository,
        runtime_bindings: RuntimeBindingResolutionService,
        chat_repository: ExpertChatRepository,
    ) -> None:
        self._chat_repository = chat_repository
        self._bindings = binding_repository
        self._runtime_bindings = runtime_bindings

    def validate(
        self,
        *,
        bot_id: str,
        owner_id: str,
        caller_user_id: str,
        binding_id: int | None,
        stage: str,
        session_key: str | None,
    ) -> None:
        if binding_id is None:
            binding_id = self._runtime_bindings.resolve(
                RuntimeBindingRequest(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    actor_user_id=caller_user_id,
                    stage=stage,
                    target=RuntimeBindingTarget.CALLER_SERVICE,
                )
            ).binding_id
        binding = self._bindings.get_by_id(binding_id)
        if binding is None or binding.device_provider != "teclaw":
            return
        if session_key is None or not session_key.strip():
            logger.warning(
                "caller_session_key_rejected bot_id=%s binding_id=%s session_key=%s reason=missing_session_key",
                bot_id, binding_id, session_key,
            )
            raise CallerCredentialError(CALLER_CREDENTIAL_REQUEST_INVALID)
        logger.info(
            "caller_session_key_validated bot_id=%s binding_id=%s session_key=%s",
            bot_id, binding_id, session_key,
        )

    def validate_owned_shared_session(
        self,
        *,
        bot_id: str,
        owner_id: str,
        caller_user_id: str,
        binding_id: int,
        stage: str,
        session_key: str | None,
    ) -> None:
        fields = {
            "bot_id": bot_id, "owner_id": owner_id, "caller_user_id": caller_user_id,
            "binding_id": binding_id, "stage": stage, "session_key": session_key,
        }
        binding = self._bindings.get_by_id(binding_id)
        if binding is None or binding.device_provider != "teclaw" or binding.status != "ACTIVE":
            logger.warning("caller_shared_session_rejected fields=%s reason=target_unavailable", fields)
            raise CallerCredentialError(CALLER_TARGET_NOT_FOUND)
        if session_key is None or not session_key.strip():
            logger.warning("caller_shared_session_rejected fields=%s reason=missing_session_key", fields)
            raise CallerCredentialError(CALLER_CREDENTIAL_REQUEST_INVALID)
        # COSEC: shared runtimes must authorize the exact active session subject,
        # not a user suffix or ownership of the shared device itself.
        if not self._chat_repository.get_owned_session(caller_user_id, bot_id, owner_id, session_key):
            logger.warning("caller_shared_session_rejected fields=%s reason=session_not_owned", fields)
            raise CallerIdentityPermissionError
        logger.info("caller_shared_session_validated fields=%s", fields)
