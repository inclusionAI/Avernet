"""Relay Service API v1: detached snapshots, transition fences and failures."""

from datetime import UTC, datetime
from typing import Literal
from unittest.mock import MagicMock

import pytest

from secbaas.community.api.device_manage import DeviceCreationError
from secbaas.community.api.paas import WsRelaySessionInfo
from secbaas.community.api.paas import WsRelaySessionService as ServiceAPI
from secbaas.community.core.repository.ws_relay_session import (
    WsRelaySessionRecord,
    WsRelaySessionRepository,
)
from secbaas.community.core.service.ws_relay_session import WsRelaySessionService


@pytest.fixture
def repository():
    now = datetime(2026, 10, 29, tzinfo=UTC)
    repo = MagicMock(spec=WsRelaySessionRepository)
    repo.get_by_session_id.return_value = WsRelaySessionRecord(
        id=1,
        gmt_create=now,
        gmt_modified=now,
        session_id="s1",
        machine_id="m1",
        connected_server_instance="",
        status="init",
        env="test",
        gmt_close=None,
        connected_route_info=None,
        operator="u1",
    )
    repo.update_active.return_value = 1
    repo.update_closed.return_value = 1
    return repo


def test_read_conforms_to_detached_api_snapshot(repository):
    service: ServiceAPI = WsRelaySessionService(repository)
    assert isinstance(service, ServiceAPI)
    result = service.get_by_session_id("s1")
    assert isinstance(result, WsRelaySessionInfo)
    assert result.session_id == "s1"
    assert result.env == "test"
    assert result is not repository.get_by_session_id.return_value
    repository.get_by_session_id.assert_called_once_with("s1")


@pytest.mark.parametrize(
    ("source", "target", "method", "expected"),
    [
        ("init", "active", "update_active", frozenset({"init"})),
        ("init", "closed", "update_closed", frozenset({"init", "active"})),
        ("active", "closed", "update_closed", frozenset({"init", "active"})),
    ],
)
def test_transition_uses_repository_cas(
    repository, source, target: Literal["active", "closed"], method, expected
):
    repository.get_by_session_id.return_value.status = source
    service: ServiceAPI = WsRelaySessionService(repository)
    assert (
        service.update_status(
            session_id="s1",
            status=target,
            connected_server_instance="server1",
            connected_route_info={"port": 18789},
        )
        == target
    )
    write = getattr(repository, method)
    write.assert_called_once()
    assert write.call_args.kwargs["session_id"] == "s1"
    assert write.call_args.kwargs["expected_statuses"] == expected
    repository.get_by_session_id.assert_called_once_with("s1")


@pytest.mark.parametrize("status", ["active", "closed"])
def test_same_state_is_noop_without_route_fields(repository, status):
    repository.get_by_session_id.return_value.status = status
    service: ServiceAPI = WsRelaySessionService(repository)
    assert service.update_status(session_id="s1", status=status) == status
    repository.update_active.assert_not_called()
    repository.update_closed.assert_not_called()


@pytest.mark.parametrize(
    ("source", "server", "route", "error_code"),
    [
        ("closed", "server1", {"port": 18789}, "RELAY_STATE_CONFLICT"),
        ("init", " ", {"port": 18789}, "INVALID_PARAMS"),
        ("init", "server1", {}, "INVALID_PARAMS"),
        (None, "server1", {"port": 18789}, "RELAY_SESSION_NOT_FOUND"),
    ],
)
def test_domain_failures_do_not_write(repository, source, server, route, error_code):
    if source is None:
        repository.get_by_session_id.return_value = None
    else:
        repository.get_by_session_id.return_value.status = source
    service: ServiceAPI = WsRelaySessionService(repository)
    with pytest.raises(DeviceCreationError) as exc:
        service.update_status(
            session_id="s1",
            status="active",
            connected_server_instance=server,
            connected_route_info=route,
        )
    assert exc.value.error_code == error_code
    repository.update_active.assert_not_called()
    repository.update_closed.assert_not_called()


def test_write_failure_propagates_without_diagnostic_read_or_retry(repository):
    error = RuntimeError("commit failed")
    repository.update_closed.side_effect = error
    service: ServiceAPI = WsRelaySessionService(repository)
    with pytest.raises(RuntimeError) as exc:
        service.update_status(session_id="s1", status="closed")
    assert exc.value is error
    repository.update_closed.assert_called_once()
    repository.get_by_session_id.assert_called_once_with("s1")
