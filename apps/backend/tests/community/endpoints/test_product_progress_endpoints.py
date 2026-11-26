"""Registered real-service progress delivery and signed-identity failures."""

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.endpoints.product_progress_seeds import (
    seed_running_stage,
    signed_headers,
    REPORT,
)

PATH = "/api/v1/collaboration/task/product-progress"


@endpoint_test(
    method="POST",
    path=PATH,
    scenario="happy",
    seed=seed_running_stage,
    input=CaseInput(headers=signed_headers(), json_body=REPORT),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"accepted": True, "type": "RUN_FAILED", "revision": 3}},
    ),
)
def report_failure_happy():
    """A signed allocated executor can persist a real failure fact without artifacts."""


@endpoint_test(
    method="POST",
    path=PATH,
    scenario="invalid_attempt",
    seed=seed_running_stage,
    input=CaseInput(
        headers=signed_headers(), json_body={**REPORT, "attempt": "forged"}
    ),
    expect=ExpectError(status=409),
)
def report_invalid_attempt():
    """Authenticated does not mean allocated; the stored attempt fence remains mandatory."""


@endpoint_test(
    method="POST",
    path=PATH,
    scenario="unsigned",
    seed=seed_running_stage,
    input=CaseInput(headers={"x-user-id": "u1"}, json_body=REPORT),
    expect=ExpectError(status=401),
)
def report_unsigned():
    """The user-auth path is not an executor authentication fallback."""
