"""Relay transition policy; repository calls never span validation or diagnostics."""

from typing import Any, Literal

from secbaas.community.api.device_manage import DeviceCreationError
from secbaas.community.api.paas import WsRelaySessionInfo
from secbaas.community.core.repository.ws_relay_session import (
    WsRelaySessionRepository,
)

VALID_TRANSITIONS = frozenset(
    {("init", "active"), ("init", "closed"), ("active", "closed")}
)


class WsRelaySessionService:
    def __init__(self, repository: WsRelaySessionRepository) -> None:
        self._repository = repository

    def get_by_session_id(self, session_id: str) -> WsRelaySessionInfo:
        record = self._repository.get_by_session_id(session_id)
        if record is None:
            raise DeviceCreationError(
                error_code="RELAY_SESSION_NOT_FOUND",
                message=f"Relay session {session_id} not found",
            )
        return WsRelaySessionInfo(
            id=record.id,
            gmt_create=record.gmt_create,
            gmt_modified=record.gmt_modified,
            session_id=record.session_id,
            machine_id=record.machine_id,
            connected_server_instance=record.connected_server_instance,
            status=record.status,
            env=record.env,
            gmt_close=record.gmt_close,
            connected_route_info=record.connected_route_info,
            operator=record.operator,
        )

    @staticmethod
    def _validate_transition(current: str, target: str) -> None:
        if current == target or (current, target) in VALID_TRANSITIONS:
            return
        raise DeviceCreationError(
            error_code="RELAY_STATE_CONFLICT",
            message=f"Invalid state transition: {current} -> {target} is not allowed. "
            "Closed sessions cannot be reopened.",
        )

    def update_status(
        self,
        *,
        session_id: str,
        status: Literal["active", "closed"],
        connected_server_instance: str | None = None,
        connected_route_info: dict[str, Any] | None = None,
    ) -> str:
        record = self.get_by_session_id(session_id)
        # Preserve no-op semantics: don't overwrite routing or reset close time.
        if record.status == status:
            return status
        self._validate_transition(record.status, status)
        expected_statuses = frozenset(
            src for src, dst in VALID_TRANSITIONS if dst == status
        )
        if status == "active":
            if not connected_server_instance or not connected_server_instance.strip():
                raise DeviceCreationError(
                    error_code="INVALID_PARAMS",
                    message="connected_server_instance is required for active status",
                )
            if not connected_route_info:
                raise DeviceCreationError(
                    error_code="INVALID_PARAMS",
                    message="connected_route_info is required for active status",
                )
            changed = self._repository.update_active(
                session_id=session_id,
                connected_server_instance=connected_server_instance,
                connected_route_info=connected_route_info,
                expected_statuses=expected_statuses,
            )
        else:
            changed = self._repository.update_closed(
                session_id=session_id, expected_statuses=expected_statuses
            )
        if changed:
            return status
        # A later snapshot, read only after the write session has returned.
        latest = self.get_by_session_id(session_id)
        if latest.status == status:
            return status
        raise DeviceCreationError(
            error_code="RELAY_STATE_CONFLICT",
            message=f"Relay session changed concurrently: {latest.status} -> {status}; retry with fresh state.",
        )
