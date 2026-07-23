"""Repository protocol for the minimal Caller configuration state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from agentclaw.community.core.caller_identity.contracts import (
    DraftCallTypeCompensationResult,
    DraftCallTypeMutationResult,
)
from agentclaw.community.core.caller_identity.models import McpCallType


class CallerIdentityLockMismatchError(RuntimeError):
    """The lock changed after the service-layer authorization check."""


class CallerIdentityEngineChangedError(RuntimeError):
    """The Bot active engine changed while updating its MCP identity."""


@runtime_checkable
class CallerIdentityRepositoryProtocol(Protocol):
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
    ) -> DraftCallTypeMutationResult: ...

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
    ) -> DraftCallTypeCompensationResult: ...

    def list_draft_call_types(
        self,
        bot_pk: int,
        engine_type: str,
    ) -> Mapping[str, McpCallType]: ...


__all__ = [
    "CallerIdentityEngineChangedError",
    "CallerIdentityLockMismatchError",
    "CallerIdentityRepositoryProtocol",
]
