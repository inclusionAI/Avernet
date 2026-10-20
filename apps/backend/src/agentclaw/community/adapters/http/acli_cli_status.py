"""HTTP statuses for the acli CLI domain errors (``core/acli_clis``).

Kept beside ``app.py``'s ``_DOMAIN_ERROR_STATUS_MAP`` and merged into it,
because that module is already at the size cap. The public
``/openapi/v1`` routes answer these through ``ENVELOPE_ERRORS`` with the same
statuses; this table covers the app-level backstop.
"""
from __future__ import annotations

from agentclaw.community.core.acli_clis.errors import (
    AcliBotNotRunningError,
    AcliCliNameMismatchError,
    AcliContainerError,
    AcliDefaultCliError,
    AcliEngineOutdatedError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliMarketNotConfiguredError,
    AcliMarketUnavailableError,
    AcliOperationCancelledError,
    AcliOperationFailedError,
    AcliOperationInProgressError,
    AcliOperationNotActiveError,
    AcliOperationNotFoundError,
    AcliRequestRejectedError,
    AcliStageNotAllowedError,
)
from agentclaw.community.core.errors import DomainError

ACLI_CLI_ERROR_STATUS: dict[type[DomainError], int] = {
    AcliDefaultCliError: 409,
    AcliCliNameMismatchError: 422,
    AcliStageNotAllowedError: 422,
    AcliEngineUnsupportedError: 501,
    AcliBotNotRunningError: 409,
    AcliEngineOutdatedError: 409,
    AcliOperationInProgressError: 409,
    AcliOperationNotActiveError: 409,
    AcliOperationCancelledError: 409,
    AcliOperationNotFoundError: 404,
    AcliRequestRejectedError: 422,
    AcliGrantSyncError: 502,
    AcliOperationFailedError: 502,
    AcliContainerError: 502,
    AcliMarketNotConfiguredError: 501,
    AcliMarketUnavailableError: 502,
}

__all__ = ["ACLI_CLI_ERROR_STATUS"]
