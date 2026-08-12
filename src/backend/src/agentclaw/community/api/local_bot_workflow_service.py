"""Service API for public local Bot workflows."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from agentclaw.community.core.bot_inventory.types import (
    LocalAuthStatusResult,
    LocalBotCreateCommand,
)


@runtime_checkable
class LocalBotWorkflowServiceProtocol(Protocol):
    """User-scoped local Bot application workflow service."""

    def list_devices(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[int, list[Mapping[str, Any]]]: ...

    def list_device_files(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        machine_id: str,
        directory: str,
    ) -> Mapping[str, Any]: ...

    def start_create(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        command: LocalBotCreateCommand,
    ) -> Mapping[str, Any]: ...

    def list_bots(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        keyword: str | None,
        engine: str | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[Mapping[str, Any]]]: ...

    def get_bot(
        self, *, owner_id: str, header_space_id: str | None, bot_id: str
    ) -> Mapping[str, Any]: ...

    def poll_auth_status(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        bot_id: str,
        command: LocalBotCreateCommand,
    ) -> LocalAuthStatusResult: ...

    def restart(
        self, *, owner_id: str, header_space_id: str | None, bot_id: str
    ) -> Mapping[str, Any]: ...

    def delete(self, *, owner_id: str, header_space_id: str | None, bot_id: str) -> None: ...

    def open_folder(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        bot_id: str,
        folder_path: str | None,
    ) -> Mapping[str, Any]: ...
