from unittest.mock import MagicMock

import pytest

from secbaas.community.core.service.ws_relay_session import WsRelaySessionService


@pytest.fixture
def service():
    return WsRelaySessionService(MagicMock())


class TestValidateTransition:
    """Tests for _validate_transition (Test 13)."""

    def test_init_to_active_allowed(self, service):
        """init -> active is a valid transition."""
        # Should not raise
        service._validate_transition("init", "active")

    def test_active_to_closed_allowed(self, service):
        """active -> closed is a valid transition."""
        service._validate_transition("active", "closed")

    def test_init_to_closed_allowed(self, service):
        """init -> closed is a valid transition."""
        service._validate_transition("init", "closed")

    def test_same_status_idempotent(self, service):
        """Same -> same is idempotent (no-op)."""
        service._validate_transition("init", "init")
        service._validate_transition("active", "active")
        service._validate_transition("closed", "closed")

    def test_closed_to_active_raises(self, service):
        """closed -> active is a reverse transition, must raise."""
        from secbaas.community.api.device_manage._errors import DeviceCreationError

        with pytest.raises(DeviceCreationError) as exc_info:
            service._validate_transition("closed", "active")
        assert exc_info.value.error_code == "RELAY_STATE_CONFLICT"

    def test_active_to_init_raises(self, service):
        """active -> init is a reverse transition, must raise."""
        from secbaas.community.api.device_manage._errors import DeviceCreationError

        with pytest.raises(DeviceCreationError):
            service._validate_transition("active", "init")

    def test_closed_to_init_raises(self, service):
        """closed -> init is a reverse transition, must raise."""
        from secbaas.community.api.device_manage._errors import DeviceCreationError

        with pytest.raises(DeviceCreationError):
            service._validate_transition("closed", "init")

    def test_closed_to_closed_idempotent(self, service):
        """closed -> closed is idempotent, no raise."""
        service._validate_transition("closed", "closed")


def test_read_returns_api_snapshot_without_exposing_repository_record():
    from dataclasses import asdict
    from datetime import UTC, datetime

    from secbaas.community.api.paas import (
        WsRelaySessionInfo,
    )
    from secbaas.community.api.paas import (
        WsRelaySessionService as ServiceAPI,
    )
    from secbaas.community.core.repository.ws_relay_session import WsRelaySessionRecord

    now = datetime(2026, 10, 29, tzinfo=UTC)
    record = WsRelaySessionRecord(
        id=1,
        gmt_create=now,
        gmt_modified=now,
        session_id="s1",
        machine_id="m1",
        connected_server_instance="server",
        status="active",
        env="dev",
        gmt_close=None,
        connected_route_info={"port": 18789},
        operator="u1",
    )
    repository = MagicMock()
    repository.get_by_session_id.return_value = record
    service = WsRelaySessionService(repository)
    assert isinstance(service, ServiceAPI)
    snapshot = service.get_by_session_id("s1")
    assert isinstance(snapshot, WsRelaySessionInfo)
    assert snapshot is not record
    assert asdict(snapshot) == asdict(record)
    repository.get_by_session_id.assert_called_once_with("s1")


def test_read_unknown_session_raises_domain_not_found(service):
    from secbaas.community.api.device_manage import DeviceCreationError

    service._repository.get_by_session_id.return_value = None
    with pytest.raises(DeviceCreationError) as exc:
        service.get_by_session_id("missing")
    assert exc.value.error_code == "RELAY_SESSION_NOT_FOUND"
