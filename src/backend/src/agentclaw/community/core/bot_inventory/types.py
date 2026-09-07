"""Domain DTOs for personal/local Bot inventory views."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class DeployMode(StrEnum):
    CLOUD = "cloud"
    LOCAL = "local"


class BotInventoryKind(StrEnum):
    PERSONAL_CLOUD = "personal_cloud"
    LOCAL = "local"
    SERVICE = "service"


class DisplayState(StrEnum):
    RUNNING = "running"
    PENDING = "pending"
    FAILED = "failed"
    DORMANT = "dormant"
    LOCAL_RUNNING = "local_running"
    LOCAL_OFFLINE = "local_offline"
    LOCAL_PENDING = "local_pending"
    LOCAL_FAILED = "local_failed"
    SERVICE_DRAFT = "service_draft"
    SERVICE_DEPLOYING = "service_deploying"
    SERVICE_PRESTABLE = "service_prestable"
    # Retained for clients generated from the pre-PD contract. New service
    # projections use the two precise states above.
    SERVICE_STAGING = "service_staging"
    SERVICE_ONLINE = "service_online"
    SERVICE_OFFLINE = "service_offline"


class BotAction(StrEnum):
    VIEW = "view"
    CHAT = "chat"
    EDIT = "edit"
    DELETE = "delete"
    RESTART = "restart"
    RESTART_PUBLISH = "restart_publish"
    DATA_INIT = "data_init"
    ACTIVATE = "activate"
    OPEN_FOLDER = "open_folder"
    PASSPORT = "passport"
    ENGINE_CONFIG = "engine_config"
    RUNTIME_LOGS = "runtime_logs"
    ENGINE_RESTART = "engine_restart"
    PUBLISH_STAGING = "publish_staging"
    PUBLISH_ONLINE = "publish_online"
    CANCEL_STAGING = "cancel_staging"
    UPGRADE = "upgrade"
    OFFLINE = "offline"
    RETRY = "retry"


@dataclass(frozen=True)
class BusinessSpaceRef:
    space_id: str
    name: str
    kind: str

    @property
    def numeric_id(self) -> int | None:
        """Return the real Space id, or None for a synthetic personal fallback."""
        if self.space_id.startswith("personal:"):
            return None
        return int(self.space_id, 10)


@dataclass(frozen=True)
class ServiceLifecycleCard:
    """One publish-version card contributed by the service lifecycle owner."""

    publication_id: int | None
    version: int | None
    display_state: DisplayState
    status: str
    actions: tuple[BotAction, ...]
    internal_status: str | None = None
    live_version: int | None = None
    has_draft: bool = False


@dataclass(frozen=True)
class ServiceEditLockState:
    """Bot-level collaborative edit-lock state used by inventory cards."""

    locked: bool
    holder_user_id: str | None
    holder_name: str | None
    has_collaborators: bool
    is_owner_holder: bool
    need_lock: bool = False


@dataclass(frozen=True)
class BotInventoryItem:
    bot_id: str
    bot_name: str
    bot_desc: str
    engine: str
    bot_type: str
    kind: BotInventoryKind
    deploy_mode: DeployMode
    display_state: DisplayState
    status: str
    owner_entity_id: str
    space: BusinessSpaceRef | None
    avatar_url: str | None = None
    machine_id: str | None = None
    mount_path: str | None = None
    passport_id: str | None = None
    actions: tuple[BotAction, ...] = ()
    disabled_actions: Mapping[str, str] | None = None
    card_id: str = ""
    publication_id: int | None = None
    publication_version: int | None = None
    live_version: int | None = None
    internal_status: str | None = None
    edit_lock: ServiceEditLockState | None = None
    # Template identity (engine stays the engine vocabulary; coding identity
    # lives in template_type + the public projection of template_config).
    template_type: str | None = None
    template_config: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LocalBotCreateCommand:
    bot_name: str
    machine_id: str
    bot_desc: str | None = None
    mount_path: str | None = None
    avatar_url: str | None = None
    engine: str = "openclaw"


@dataclass(frozen=True)
class LocalAuthStatusResult:
    status: str
    message: str | None = None
    bot: Mapping[str, Any] | None = None
    client_error: bool = False
