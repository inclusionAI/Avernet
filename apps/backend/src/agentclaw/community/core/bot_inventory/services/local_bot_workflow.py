"""Core workflow for public local Bot operations.

HTTP adapters should remain thin: this service owns local business-space
policy, local listing filters/pagination, Passport authorization polling, and
post-creation authorization relationship creation.
"""

from __future__ import annotations

from typing import Any, Mapping

from agentclaw.community.core.bot_inventory.errors import (
    BotInventoryOperationNotAllowedError,
    BotInventoryPermissionError,
    BotInventoryUpstreamError,
)
from agentclaw.community.core.bot_inventory.policies.combo_policy import (
    assert_local_create,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
    DesktopBotInventoryPort,
)
from agentclaw.community.core.bot_inventory.types import (
    LocalAuthStatusResult,
    LocalBotCreateCommand,
)
from agentclaw.community.core.errors import NotFound
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.core.bot_inventory.local_bot_workflow_service_protocol import LocalBotWorkflowServiceProtocol


class LocalBotWorkflowService(LocalBotWorkflowServiceProtocol):
    """Application service for personal local Bot public workflows."""

    def __init__(
        self,
        *,
        desktop_service: DesktopBotInventoryPort,
        business_space: BusinessSpaceContextProtocol,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
    ) -> None:
        self._desktop = desktop_service
        self._business_space = business_space
        self._passport = passport_plugin
        self._auth_relationship = auth_relationship_plugin

    def require_personal_space(self, *, owner_id: str, header_space_id: str | None):
        space = self._business_space.resolve_current(
            owner_id=owner_id,
            header_space_id=header_space_id,
        )
        if space.kind != "personal":
            raise BotInventoryOperationNotAllowedError(
                "local bots are personal business-space only"
            )
        return space

    def list_devices(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[int, list[Mapping[str, Any]]]:
        self.require_personal_space(owner_id=owner_id, header_space_id=header_space_id)
        try:
            return self._desktop.list_devices(
                user_id=owner_id,
                page=page,
                page_size=page_size,
                status=status,
            )
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise

    def list_device_files(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        machine_id: str,
        directory: str,
    ) -> Mapping[str, Any]:
        self.require_personal_space(owner_id=owner_id, header_space_id=header_space_id)
        try:
            return self._desktop.list_directory(machine_id=machine_id, dir=directory)
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise

    def start_create(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        command: LocalBotCreateCommand,
    ) -> Mapping[str, Any]:
        space = self.require_personal_space(
            owner_id=owner_id, header_space_id=header_space_id
        )
        decision = assert_local_create(command.engine, space.kind)
        if not decision.ok:
            raise BotInventoryOperationNotAllowedError(
                decision.reason or "local bot create is not allowed"
            )
        try:
            return self._desktop.apply_passport_before_create(
                bot={"bot_name": command.bot_name, "bot_desc": command.bot_desc},
                user_id=owner_id,
                machine_id=command.machine_id,
                mount_path=command.mount_path,
                avatar_url=command.avatar_url,
                engine_type=command.engine,
            )
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise

    def list_bots(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        keyword: str | None,
        engine: str | None,
        bot_ids: list[str] | None = None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[Mapping[str, Any]]]:
        self.require_personal_space(owner_id=owner_id, header_space_id=header_space_id)
        try:
            rows = list(self._desktop.list_user_bots(owner_id))
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise
        if bot_ids is not None:
            allowed = frozenset(bot_ids)
            rows = [row for row in rows if str(row.get("bot_id") or "") in allowed]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("bot_name") or "")]
        if engine:
            rows = [
                row
                for row in rows
                if (
                    row.get("active_engine")
                    or row.get("engine_type")
                    or row.get("engine")
                )
                == engine
            ]
        total = len(rows)
        start = (page - 1) * page_size
        return total, rows[start : start + page_size]

    def get_bot(
        self, *, owner_id: str, header_space_id: str | None, bot_id: str
    ) -> Mapping[str, Any]:
        self.require_personal_space(owner_id=owner_id, header_space_id=header_space_id)
        return self._find_visible_bot(owner_id=owner_id, bot_id=bot_id)

    def poll_auth_status(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        bot_id: str,
        command: LocalBotCreateCommand,
    ) -> LocalAuthStatusResult:
        space = self.require_personal_space(
            owner_id=owner_id, header_space_id=header_space_id
        )
        decision = assert_local_create(command.engine, space.kind)
        if not decision.ok:
            raise BotInventoryOperationNotAllowedError(
                decision.reason or "local bot create is not allowed"
            )
        auth_status = self._passport.query_auth_status(
            bot_id=bot_id, owner_workno=owner_id
        )
        if not auth_status:
            raise BotInventoryOperationNotAllowedError(
                "authorization status unavailable"
            )
        status_value = str(auth_status.get("status") or "")
        if status_value == "PENDING":
            return LocalAuthStatusResult(
                status="PENDING",
                message="Authorization pending",
            )
        if status_value != "ISSUED":
            return LocalAuthStatusResult(
                status=status_value,
                message="Authorization did not complete",
                client_error=True,
            )

        try:
            result = self._desktop.create_after_authorization(
                bot={
                    "bot_id": bot_id,
                    "bot_name": command.bot_name,
                    "bot_desc": command.bot_desc,
                    "avatar_url": command.avatar_url,
                },
                user_id=owner_id,
                machine_id=command.machine_id,
                mount_path=command.mount_path,
                engine_type=command.engine,
            )
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise
        agent_code = result.get("agent_code")
        if not isinstance(agent_code, str) or not agent_code.strip():
            raise BotInventoryUpstreamError("desktop creation returned no agent_code")
        # This relationship is part of the completed creation contract. Surface
        # failures instead of acknowledging ISSUED with missing authorization
        # state; durable cross-system repair remains a separate responsibility.
        try:
            relationship = self._auth_relationship.create_relationship(
                work_no=owner_id,
                agent_code=agent_code,
                description="Bot owner default authorization",
                operator_work_no=owner_id,
                operator_name=owner_id,
            )
        except Exception as exc:  # noqa: BLE001 — normalize plugin implementations
            raise BotInventoryUpstreamError(
                "authorization relationship write failed"
            ) from exc
        if relationship is None:
            raise BotInventoryUpstreamError("authorization relationship write failed")
        return LocalAuthStatusResult(status="ISSUED", bot=result)

    def restart(
        self, *, owner_id: str, header_space_id: str | None, bot_id: str
    ) -> Mapping[str, Any]:
        self.require_personal_space(owner_id=owner_id, header_space_id=header_space_id)
        self._verify_visible(owner_id=owner_id, bot_id=bot_id)
        try:
            return self._desktop.restart(bot_id=bot_id, user_id=owner_id)
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise

    def delete(
        self, *, owner_id: str, header_space_id: str | None, bot_id: str
    ) -> None:
        self.require_personal_space(owner_id=owner_id, header_space_id=header_space_id)
        self._verify_visible(owner_id=owner_id, bot_id=bot_id)
        try:
            self._desktop.delete(bot_id=bot_id, user_id=owner_id)
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise

    def open_folder(
        self,
        *,
        owner_id: str,
        header_space_id: str | None,
        bot_id: str,
        folder_path: str | None,
    ) -> Mapping[str, Any]:
        self.require_personal_space(owner_id=owner_id, header_space_id=header_space_id)
        self._verify_visible(owner_id=owner_id, bot_id=bot_id)
        try:
            return self._desktop.open_folder(
                bot_id=bot_id,
                user_id=owner_id,
                folder_path=folder_path,
            )
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise

    def _find_visible_bot(self, *, owner_id: str, bot_id: str) -> Mapping[str, Any]:
        self._verify_visible(owner_id=owner_id, bot_id=bot_id)
        try:
            rows = self._desktop.list_user_bots(owner_id)
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise
        for row in rows:
            if str(row.get("bot_id") or "") == bot_id:
                return row
        raise BotInventoryPermissionError("bot is not visible")

    def _verify_visible(self, *, owner_id: str, bot_id: str) -> None:
        try:
            self._desktop.verify_ownership(bot_id=bot_id, user_id=owner_id)
        except NotFound as exc:
            raise BotInventoryPermissionError("bot is not visible") from exc
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise


def _raise_if_desktop_service_error(exc: Exception) -> None:
    if exc.__class__.__name__ in {"DesktopBotServiceError", "DesktopBotOrphanError"}:
        raise BotInventoryUpstreamError("desktop service failed") from exc
