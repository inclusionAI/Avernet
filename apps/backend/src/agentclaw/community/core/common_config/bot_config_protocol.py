"""Bot-scoped JSON configuration and atomic initialization contracts."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol, Any, TYPE_CHECKING, TypeAlias, runtime_checkable

from agentclaw.community.core.common_config.models import BotCommonConfigRecord

if TYPE_CHECKING:
    # Importing these at runtime cycles through service_bot's package __init__
    # (services/__init__ -> bot_publish_service -> di -> bot_service -> here).
    # PEP 563 keeps the annotations unquoted without evaluating them.
    from agentclaw.community.core.service_bot.services.deploy.deploy_models import (
        Storage,
        StorageType,
    )
    from agentclaw.community.core.service_bot.services.deploy.deploy_config_composer import (
        BotDeployContext,
    )


JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


@dataclass(frozen=True)
class BotCommonConfigEntry:
    """One batch-upsert item; the caller supplies ``env`` separately."""

    bot_id: str
    entity_id: str
    config_key: str
    value: "JsonValue"


@runtime_checkable
class BotCommonConfigServiceProtocol(Protocol):
    def get_config(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str
    ) -> JsonValue:
        """Return decoded JSON; None means an absent config or JSON null."""
        ...

    def set_config(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str, value: JsonValue
    ) -> None: ...

    # Operator-facing management operations. The service owns the on-disk
    # JSON format of ``config_value``; callers pass decoded ``JsonValue``.

    def get_record_by_id(self, *, config_id: int) -> BotCommonConfigRecord | None: ...

    def list_records(
        self,
        *,
        bot_id: str | None = None,
        entity_id: str | None = None,
        env: str | None = None,
        config_key: str | None = None,
        page_num: int = 1,
        page_size: int = 100,
    ) -> tuple[int, list[BotCommonConfigRecord]]: ...

    def create_record(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str, value: JsonValue
    ) -> int: ...

    def update_record(self, *, config_id: int, value: JsonValue) -> bool: ...

    def delete_record(self, *, config_id: int) -> bool: ...

    def upsert_record(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str, value: JsonValue
    ) -> int: ...

    def batch_upsert_records(
        self, *, env: str, records: list[BotCommonConfigEntry]
    ) -> list[int]: ...


@dataclass(frozen=True)
class StoragePolicy:
    """Saved storage choice, or an unsaved NAS fallback.

    ``source`` records how the choice was made, not creation success:
    ``rollout`` means new creation matched the UPFS rollout; ``manual`` denotes
    an operator-supplied policy. Empty means unspecified (including NAS fallback
    and older rows). Example persisted JSON:
    ``{"storage_type": "upfs", "source": "rollout"}``.
    """

    storage_type: StorageType
    source: str = ""


@dataclass(frozen=True)
class PreparedBotStoragePolicy:
    """Request-local creation choice; never persisted or sent to BaaS."""

    template_uid: str
    template_uuid: str
    storage_type: StorageType


class BotStoragePolicyProtocol(Protocol):
    def prepare_bot_storage_policy(self, **kwargs: Any) -> dict[str, Any]:
        """Choose provider/template and initialize policy before original allocation."""
        ...

    def resolve_deploy_context(self, ctx: BotDeployContext) -> BotDeployContext:
        """Resolve saved storage and its mount layout without running rollout."""
        ...

    def apply_to_storage(
        self, storage: Storage, ctx: BotDeployContext
    ) -> Storage:
        """Apply the resolved context and shared quota without reading rollout."""
        ...

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
        user_id: str,
        upfs_ready: Callable[[], bool],
    ) -> StoragePolicy:
        """Reuse an existing policy, or atomically persist a selected UPFS policy.

        NAS is returned without a write. Existing choices skip rollout/readiness;
        persistence failures propagate.
        No device creation or creation-result recording occurs in this method.
        """
        ...
