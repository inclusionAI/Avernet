"""Replaceable seams consumed by the Bot inventory core."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_inventory.types import (
    BotAction,
    BusinessSpaceRef,
    DisplayState,
    ServiceLifecycleCard,
)


@runtime_checkable
class BotInventoryBotPort(Protocol):
    """Core-side port for Bot management data consumed by inventory.

    Defined inside the consuming core module so core does not depend on the
    outward Service API package. DI binds the concrete BotService here.
    """

    def get_bot(self, bot_id: str, user_id: str) -> Mapping[str, Any]: ...

    def list_bots_by_conditions(
        self,
        public: str | None = None,
        bot_name: str | None = None,
        owner_name: str | None = None,
        bot_id: str | None = None,
        owner_id: str | None = None,
        engine: str | None = None,
        status: str | None = None,
        space_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        bot_ids: list[str] | None = None,
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class DesktopBotInventoryPort(Protocol):
    """Core-side port for local desktop Bot data and lifecycle commands."""

    def list_user_bots(self, user_id: str) -> list[dict[str, Any]]: ...

    def verify_ownership(self, *, bot_id: str, user_id: str) -> None: ...

    def list_directory(
        self,
        *,
        machine_id: str,
        dir: str = "",
    ) -> dict[str, Any]: ...

    def list_devices(
        self,
        *,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]: ...

    def apply_passport_before_create(
        self,
        bot: dict[str, Any],
        user_id: str,
        machine_id: str,
        mount_path: str | None = None,
        avatar_url: str | None = None,
        engine_type: str | None = None,
    ) -> dict[str, Any]: ...

    def create_after_authorization(
        self,
        bot: dict[str, Any],
        user_id: str,
        machine_id: str,
        migration_path: str | None = None,
        mount_path: str | None = None,
        engine_type: str | None = None,
    ) -> dict[str, Any]: ...

    def restart(self, bot_id: str, user_id: str) -> dict[str, Any]: ...

    def delete(self, bot_id: str, user_id: str) -> dict[str, Any]: ...

    def open_folder(
        self, *, bot_id: str, user_id: str, folder_path: str | None = None
    ) -> dict[str, Any]: ...


@runtime_checkable
class BotInventoryAccessPort(Protocol):
    """Batch Bot permission projection consumed by the inventory read model."""

    def get_operable_permission_levels(
        self,
        *,
        bots: Sequence[Mapping[str, Any]],
        user_id: str,
        env: str | None = None,
    ) -> dict[int, PermissionLevel]: ...


@runtime_checkable
class BusinessSpaceContextProtocol(Protocol):
    """Minimal consumer-side business-space context API.

    Bot inventory is not the business-space owner.  Implementations may delegate
    to the owning module's official service API; the local fallback only models
    the personal space.
    """

    def resolve_current(
        self, *, owner_id: str, header_space_id: str | None
    ) -> BusinessSpaceRef: ...

    def bot_space(
        self,
        *,
        bot: Mapping[str, Any],
        owner_id: str,
        current_space: BusinessSpaceRef | None = None,
    ) -> BusinessSpaceRef | None: ...

    def assert_bot_visible_in_current_space(
        self, *, bot: Mapping[str, Any], owner_id: str, current_space: BusinessSpaceRef
    ) -> None: ...


@runtime_checkable
class ServiceLifecyclePort(Protocol):
    """Service-bot lifecycle seam; real implementation belongs to service line."""

    def display_state(self, *, bot: Mapping[str, Any]) -> DisplayState: ...

    def allowed_actions(self, *, bot: Mapping[str, Any]) -> Sequence[BotAction]: ...

    def cards_for_bots(
        self, *, bots: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Sequence[ServiceLifecycleCard]]: ...
