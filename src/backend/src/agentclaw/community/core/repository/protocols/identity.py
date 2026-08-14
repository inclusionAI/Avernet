"""Repository contracts owned by the ``identity`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.access.models import AccessControlPolicyRecord, ConfigItemRecord, UserInfoRecord
    from agentclaw.community.core.caller_identity.contracts import DraftCallTypeCompensationResult, DraftCallTypeMutationResult
    from agentclaw.community.core.caller_identity.models import McpCallType


@runtime_checkable
class PolicyRepository(Protocol):
    @abstractmethod
    def get_by_entity(self, *, entity_id: str, entity_type: str) -> AccessControlPolicyRecord | None:
        ...

    @abstractmethod
    def upsert_policy(self, *, entity_id: str, entity_type: str, policy: str) -> None:
        ...

    @abstractmethod
    def get_config_by_key(self, *, config_key: str, category: str, env: str) -> ConfigItemRecord | None:
        ...

    @abstractmethod
    def count_active_devices(self, *, env: str) -> int:
        ...

    # 用户表相关方法
    @abstractmethod
    def get_user_info(self, *, user_id: str, user_type: str) -> UserInfoRecord | None:
        ...

    @abstractmethod
    def list_users(self, *, user_type: str | None = None) -> list[UserInfoRecord]:
        ...

    @abstractmethod
    def upsert_user_info(self, *, user_id: str, user_type: str, status: str) -> None:
        ...

    @abstractmethod
    def count_compete_users_after_time(self, *, start_time: str) -> int:
        ...


@runtime_checkable
class CallerIdentityRepositoryProtocol(Protocol):
    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
    def list_draft_call_types(
        self,
        bot_pk: int,
        engine_type: str,
    ) -> Mapping[str, McpCallType]: ...


@runtime_checkable
class UserListRepositoryProtocol(Protocol):
    """Read the current environment's exact membership record."""

    @abstractmethod
    def exists(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        env: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def set_membership(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        in_whitelist: bool,
        env: str | None = None,
    ) -> None: ...
