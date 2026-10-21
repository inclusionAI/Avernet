"""Adapt desktop lifecycle failures to the inventory port's error contract."""

from __future__ import annotations

from typing import Any

import httpx
from agentclaw.community.core.bot_inventory.protocols import DesktopBotInventoryPort
from agentclaw.community.core.desktop_bot.errors import DesktopBotServiceError
from agentclaw.community.core.service_bot.baas_service_errors import BaasServiceError


class DesktopInventoryAdapter(DesktopBotInventoryPort):
    """Preserve desktop operations and normalize known restart upstream errors."""

    def __init__(self, desktop: DesktopBotInventoryPort) -> None:
        self._desktop = desktop

    def list_user_bots(self, user_id: str) -> list[dict[str, Any]]:
        return self._desktop.list_user_bots(user_id=user_id)

    def verify_ownership(self, *, bot_id: str, user_id: str) -> None:
        self._desktop.verify_ownership(bot_id=bot_id, user_id=user_id)

    def list_directory(self, *, machine_id: str, dir: str = "") -> dict[str, Any]:
        return self._desktop.list_directory(machine_id=machine_id, dir=dir)

    def list_devices(
        self,
        *,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        return self._desktop.list_devices(
            user_id=user_id,
            page=page,
            page_size=page_size,
            status=status,
        )

    def apply_passport_before_create(
        self,
        bot: dict[str, Any],
        user_id: str,
        machine_id: str,
        mount_path: str | None = None,
        avatar_url: str | None = None,
        engine_type: str | None = None,
    ) -> dict[str, Any]:
        return self._desktop.apply_passport_before_create(
            bot=bot,
            user_id=user_id,
            machine_id=machine_id,
            mount_path=mount_path,
            avatar_url=avatar_url,
            engine_type=engine_type,
        )

    def create_after_authorization(
        self,
        bot: dict[str, Any],
        user_id: str,
        machine_id: str,
        migration_path: str | None = None,
        mount_path: str | None = None,
        engine_type: str | None = None,
    ) -> dict[str, Any]:
        return self._desktop.create_after_authorization(
            bot=bot,
            user_id=user_id,
            machine_id=machine_id,
            migration_path=migration_path,
            mount_path=mount_path,
            engine_type=engine_type,
        )

    def restart(self, bot_id: str, user_id: str) -> dict[str, Any]:
        try:
            return self._desktop.restart(bot_id=bot_id, user_id=user_id)
        except (BaasServiceError, httpx.HTTPError) as exc:
            raise DesktopBotServiceError("desktop restart upstream failed") from exc

    def delete(self, bot_id: str, user_id: str) -> dict[str, Any]:
        return self._desktop.delete(bot_id=bot_id, user_id=user_id)

    def open_folder(
        self,
        *,
        bot_id: str,
        user_id: str,
        folder_path: str | None = None,
    ) -> dict[str, Any]:
        return self._desktop.open_folder(
            bot_id=bot_id,
            user_id=user_id,
            folder_path=folder_path,
        )
