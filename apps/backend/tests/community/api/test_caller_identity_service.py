"""Public API contract tests for Caller identity services."""

from agentclaw.community.api import caller_identity_service
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityIrreversibleError as CoreCallerIdentityIrreversibleError,
)


def test_caller_identity_service_reexports_irreversible_error() -> None:
    """Consumers can handle the irreversible Caller transition through the API module."""
    assert (
        caller_identity_service.CallerIdentityIrreversibleError
        is CoreCallerIdentityIrreversibleError
    )
    assert "CallerIdentityIrreversibleError" in caller_identity_service.__all__
