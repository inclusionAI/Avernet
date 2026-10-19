"""HTTP statuses for the acli CLI domain errors (``core/acli_clis``).

Kept beside ``app.py``'s ``_DOMAIN_ERROR_STATUS_MAP`` and merged into it,
because that module is already at the size cap. The public
``/openapi/v1`` routes answer these through ``ENVELOPE_ERRORS`` with the same
statuses; this table covers the app-level backstop.
"""
from __future__ import annotations

from agentclaw.community.core.acli_clis.errors import (
    AcliBotNotRunningError,
    AcliCliAlreadyGrantedError,
    AcliCliNameMismatchError,
    AcliCliNotGrantedError,
    AcliContainerError,
    AcliDefaultCliError,
    AcliEngineOutdatedError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliMarketNotConfiguredError,
    AcliMarketUnavailableError,
    AcliOperationFailedError,
    AcliRequestRejectedError,
    AcliRollbackFailedError,
)
from agentclaw.community.core.errors import DomainError

ACLI_CLI_ERROR_STATUS: dict[type[DomainError], int] = {
    AcliCliAlreadyGrantedError: 409,
    AcliCliNotGrantedError: 404,
    AcliDefaultCliError: 409,
    AcliCliNameMismatchError: 422,
    AcliEngineUnsupportedError: 501,
    AcliBotNotRunningError: 409,
    AcliEngineOutdatedError: 409,
    AcliRequestRejectedError: 422,
    AcliOperationFailedError: 502,
    AcliContainerError: 502,
    AcliGrantSyncError: 502,
    AcliMarketNotConfiguredError: 501,
    AcliMarketUnavailableError: 502,
    AcliRollbackFailedError: 500,
}

__all__ = ["ACLI_CLI_ERROR_STATUS"]
