"""Endpoint tests for GET /api/v1/token/iam — return raw IAM_TOKEN cookie.

Two cases:
- **ok**: IAM_TOKEN cookie present → 200 with the raw value.
- **missing**: No IAM_TOKEN cookie → 400 error.

This endpoint has no plugin dependency or seed — it reads directly
from the request cookie. The function bodies are intentionally empty.
"""
from __future__ import annotations

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


@endpoint_test(
    method="GET",
    path="/api/v1/token/iam",
    scenario="ok",
    input=CaseInput(headers={"cookie": "IAM_TOKEN=raw-iam-token-value"}),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "iam_token": "raw-iam-token-value"},
    ),
)
def get_iam_token_ok():
    """IAM_TOKEN cookie present → return raw value."""


@endpoint_test(
    method="GET",
    path="/api/v1/token/iam",
    scenario="missing_cookie",
    input=CaseInput(),
    expect=ExpectError(
        status=400,
        json_contains={"success": False},
    ),
)
def get_iam_token_missing():
    """No IAM_TOKEN cookie → 400."""
