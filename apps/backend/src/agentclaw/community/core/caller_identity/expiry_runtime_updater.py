"""Preserve a longer-lived Caller credential on a dedicated instance."""
from __future__ import annotations

from agentclaw.community.core.caller_identity.credential import (
    CallerToken, CallerCredentialError, CALLER_CREDENTIAL_REQUEST_INVALID,
)
from agentclaw.community.core.caller_identity.protocols import CallerRuntimeUpdaterProtocol
from agentclaw.community.core.repository.protocols.chat import ExpertChatInstanceRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository


class ExpiryAwareCallerRuntimeUpdater:
    """Apply the instance expiry policy before delegating runtime installation."""

    def __init__(self, *, instances: ExpertChatInstanceRepository,
                 delegate: CallerRuntimeUpdaterProtocol, bindings: DeviceBindingRepository) -> None:
        self._bindings = bindings
        self._instances = instances
        self._delegate = delegate

    def update_caller_identity(
        self, *, bot_id: str, owner_user_id: str, caller_user_id: str,
        caller_token: CallerToken, stage: str, publish_id: int | None,
        entity_id: str | None = None, binding_id: int | None = None,
        is_test_exchange: bool = False, session_key: str | None = None,
    ) -> None:
        kwargs = dict(
            bot_id=bot_id, owner_user_id=owner_user_id, caller_user_id=caller_user_id,
            caller_token=caller_token,
            stage=stage, publish_id=publish_id,
            entity_id=entity_id, binding_id=binding_id,
            is_test_exchange=is_test_exchange, session_key=session_key,
        )
        if not caller_token.access_token or caller_token.subject_user_id != caller_user_id:
            raise CallerCredentialError(CALLER_CREDENTIAL_REQUEST_INVALID)
        if is_test_exchange or binding_id is None:
            self._delegate.update_caller_identity(**kwargs)
            return
        binding = self._bindings.get_by_id(binding_id)
        if binding is None or binding.apply_reason != f"caller_instance:{bot_id}":
            self._delegate.update_caller_identity(**kwargs)
            return
        # COSEC: only the authenticated Caller's dedicated binding can use its expiry.
        if binding.applied_by != caller_user_id or binding.status != "ACTIVE":
            raise CallerCredentialError(CALLER_CREDENTIAL_REQUEST_INVALID)
        with self._instances.caller_token_update(
            user_id=caller_user_id, bot_id=bot_id, owner_id=owner_user_id,
            binding_id=binding_id, expires_at=caller_token.expires_at,
        ) as install:
            if install:
                self._delegate.update_caller_identity(**kwargs)
