"""Adapter tests: BcnService transport outcomes map onto the domain sync port."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnServiceError,
)
from agentclaw.community.core.caller_identity.contracts import CallerBcsSyncError
from agentclaw.community.core.caller_identity.protocols import (
    BcsCallerConfigSyncProtocol,
)
from agentclaw.community.di.modules.caller_identity_module import (
    BcnBackedCallerConfigSync,
)


def _adapter(transport: MagicMock) -> BcnBackedCallerConfigSync:
    adapter = BcnBackedCallerConfigSync(bcn_service=transport)
    assert isinstance(adapter, BcsCallerConfigSyncProtocol)
    return adapter


def test_transport_error_maps_to_caller_bcs_sync_error() -> None:
    transport = MagicMock()
    transport.sync_bot_caller_config.side_effect = BcnServiceError("boom")

    with pytest.raises(CallerBcsSyncError) as excinfo:
        _adapter(transport).sync_caller_config(
            bot_id="bot-1", owner_id="owner-1", enabled=True, source_revision=3
        )

    assert isinstance(excinfo.value.__cause__, BcnServiceError)


def test_skip_result_is_silent_success() -> None:
    transport = MagicMock()
    transport.sync_bot_caller_config.return_value = {"skipped": True}

    result = _adapter(transport).sync_caller_config(
        bot_id="bot-1", owner_id="owner-1", enabled=True, source_revision=3
    )

    assert result is None
    transport.sync_bot_caller_config.assert_called_once_with(
        teamclaw_bot_uuid="bot-1",
        owner_workno="owner-1",
        enabled=True,
        source_revision=3,
    )


def test_applied_result_is_silent_success() -> None:
    transport = MagicMock()
    transport.sync_bot_caller_config.return_value = {
        "skipped": False,
        "sync_status": "applied",
    }

    assert (
        _adapter(transport).sync_caller_config(
            bot_id="bot-1", owner_id="owner-1", enabled=False, source_revision=4
        )
        is None
    )
