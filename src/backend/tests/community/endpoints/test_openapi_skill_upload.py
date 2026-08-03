"""Endpoint-framework coverage for the raw public Skill upload contract."""

from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/upload",
    scenario="raw_zip_requires_verified_principal",
    input=CaseInput(
        query_params={"bot_id": "bot"},
        headers={"content-type": "application/zip"},
        raw_body=b"PK\\x03\\x04",
    ),
    expect=ExpectSuccess(status=401),
)
def raw_zip_upload_is_a_real_raw_body_request():
    """The runner sends bytes and the endpoint handles the raw media type."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/upload",
    scenario="multipart_rejected_before_upload",
    input=CaseInput(
        query_params={"bot_id": "bot"},
        headers={"content-type": "multipart/form-data; boundary=x"},
        raw_body=b"--x--",
    ),
    expect=ExpectError(status=401),
)
def multipart_upload_is_exercised_as_an_error_case():
    """Auth precedes payload validation for unverified external requests."""
