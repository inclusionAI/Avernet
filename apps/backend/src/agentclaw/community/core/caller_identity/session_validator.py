"""Validate the session context required by TeClaw Caller updates."""

from __future__ import annotations

from agentclaw.community.core.caller_identity.credential import (
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CallerCredentialError,
)
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
    ) -> None:
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
