"""Bot-scoped JSON configuration and atomic initialization contracts."""

from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol, Any


class BotCommonConfigServiceProtocol(Protocol):
    def get_config(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str
    ) -> Any: ...

    def set_config(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str, value: Any
    ) -> None: ...


@dataclass(frozen=True)
class StoragePolicy:
    storage_type: str
    source: str = ""


class BotStoragePolicyProtocol(Protocol):
    def get_storage_quota(self, env: str) -> str:
        """Read quota independently of rollout; preserve the string, default to 1G."""
        ...

    def _resolve_storage_policy(
        self, bot_id: str, entity_id: str, env: str
    ) -> StoragePolicy | None: ...

    def initialize(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        engine: str,
        user_id: str | None,
        upfs_ready: Callable[[], bool],
    ) -> StoragePolicy:
        """Reuse an existing policy, or atomically persist a selected UPFS policy.

        NAS is returned without a write. Existing choices skip rollout/readiness;
        persistence failures propagate.
        No device creation or creation-result recording occurs in this method.
        """
        ...
