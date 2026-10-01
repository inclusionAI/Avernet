"""Focused error-contract tests for the OpenAPI IAM-token adapter."""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.errors import (
    CallerIdentityConflictError,
    CallerIdentityForbiddenError,
    CallerIdentityInvalidError,
    CallerIdentityOpenApiError,
    IamTokenUnavailableError,
)
from agentclaw.community.adapters.http.openapi_v1.token.router import _raise_for_error


def test_no_caller_error_returns_normally() -> None:
    _raise_for_error(None)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("IAM_TOKEN cookie not found", IamTokenUnavailableError),
        ("CALLER_CREDENTIAL_REQUEST_INVALID", CallerIdentityInvalidError),
        ("CALLER_IDENTITY_FORBIDDEN", CallerIdentityForbiddenError),
        ("CALLER_IDENTITY_AMBIGUOUS", CallerIdentityConflictError),
        ("CALLER_OUTBOUND_UPDATE_FAILED", CallerIdentityOpenApiError),
    ],
)
def test_caller_errors_map_to_fixed_openapi_error_types(
    code: str, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        _raise_for_error(code)
