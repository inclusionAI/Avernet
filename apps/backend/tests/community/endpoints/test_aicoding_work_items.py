"""Endpoint-contract tests for the user-level Agent Coding work-items API."""

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_PATH = "/api/v1/aicoding/work-items"


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="default_recent_work_items",
    input=CaseInput(
        query_params={
            "source": "mine",
            "since_ms": "1794000000000",
            "run_status": "non_idle",
            "limit": "50",
        },
        headers={"X-User-Id": "315629"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "items": [],
                "bots": [],
                "partial": False,
                "scan_limit": 50,
                "filters": {
                    "source": ["mine"],
                    "since_ms": 1794000000000,
                    "run_status": ["non_idle"],
                },
            },
        },
    ),
)
def default_recent_work_items():
    """The framework owns the request invocation."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="invalid_limit",
    input=CaseInput(
        query_params={"limit": "0"},
        headers={"X-User-Id": "315629"},
    ),
    expect=ExpectError(
        status=400,
        json_contains={"detail": {"code": "INVALID_QUERY"}},
    ),
)
def invalid_limit():
    """Numeric query errors keep the public 400 shape."""
