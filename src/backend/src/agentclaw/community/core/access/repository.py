"""Access control repository protocol."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.access.models import (
    AccessControlPolicyRecord,
    ConfigItemRecord,
    UserInfoRecord,
)


@runtime_checkable
class PolicyRepository(Protocol):
    def get_by_entity(self, *, entity_id: str, entity_type: str) -> AccessControlPolicyRecord | None:
        ...

    def upsert_policy(self, *, entity_id: str, entity_type: str, policy: str) -> None:
        ...

    def get_config_by_key(self, *, config_key: str, category: str, env: str) -> ConfigItemRecord | None:
        ...

    def count_active_devices(self, *, env: str) -> int:
        ...

    # 用户表相关方法
    def get_user_info(self, *, user_id: str, user_type: str) -> UserInfoRecord | None:
        ...

    def list_users(self, *, user_type: str | None = None) -> list[UserInfoRecord]:
        ...

    def upsert_user_info(self, *, user_id: str, user_type: str, status: str) -> None:
        ...

    def count_compete_users_after_time(self, *, start_time: str) -> int:
        ...
