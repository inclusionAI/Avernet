"""Contract checks for the BCS caller-config sync port and its domain error."""

from __future__ import annotations

from agentclaw.community.core.errors import InternalError


def _import_contracts():
    from agentclaw.community.core.caller_identity import contracts

    return contracts


def _import_protocols():
    from agentclaw.community.core.caller_identity import protocols

    return protocols


class _StubSync:
    def sync_caller_config(
        self, *, bot_id: str, owner_id: str, enabled: bool, source_revision: int
    ) -> None:
        del bot_id, owner_id, enabled, source_revision


def test_bcs_caller_sync_protocol_is_satisfied_by_method_shape() -> None:
    protocols = _import_protocols()
    assert isinstance(_StubSync(), protocols.BcsCallerConfigSyncProtocol)


def test_caller_bcs_sync_error_is_an_internal_error_family_member() -> None:
    contracts = _import_contracts()
    assert issubclass(contracts.CallerBcsSyncError, InternalError)
    assert contracts.CallerBcsSyncError().detail == "CALLER_BCS_SYNC_FAILED"
    assert "CallerBcsSyncError" in contracts.__all__
    assert "BcsCallerConfigSyncProtocol" in _import_protocols().__all__


def test_api_reexports_the_sync_port_and_error() -> None:
    from agentclaw.community.api import caller_identity_service

    contracts = _import_contracts()
    protocols = _import_protocols()
    assert "CallerBcsSyncError" in caller_identity_service.__all__
    assert "BcsCallerConfigSyncProtocol" in caller_identity_service.__all__
    assert (
        caller_identity_service.CallerBcsSyncError is contracts.CallerBcsSyncError
    )
    assert (
        caller_identity_service.BcsCallerConfigSyncProtocol
        is protocols.BcsCallerConfigSyncProtocol
    )
