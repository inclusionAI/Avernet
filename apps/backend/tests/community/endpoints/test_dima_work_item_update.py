"""Endpoint tests for POST /api/public/dima/work-items/append-file.

Tests use the DI test framework with stub service bindings.

- **ok**: full body with all fields specified.
- **ok_defaults**: minimal body (only dimaId + url), operator uses default.
- **missing_dima_id**: dimaId missing → 422.
- **missing_url**: url missing → 422.
- **service_error**: stub raises; router returns ``{"success": False, "code": "500", ...}``.
"""
from __future__ import annotations

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/public/dima/work-items/append-file"

_FULL_BODY = {
    "operator": "100000",
    "dimaId": "2026052400116279290",
    "url": "https://example.com",
}

_MINIMAL_BODY = {
    "dimaId": "2026052400116279290",
    "url": "https://example.com",
}


def _seed_ok(world):
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _StubDimaService:
        def create_work_item(self, staff_id, request_body):
            return {"success": True, "code": "200", "message": "OK", "data": 0}

        def create_work_item_relation(self, operator, request_body):
            return {"success": True, "code": "200", "message": "OK", "data": None}

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_StubDimaService,
        scope=None,
    )


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok",
    input=CaseInput(json_body=_FULL_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200", "message": "URL linked"},
    ),
)
def update_work_item_ok():
    """Happy path: full body with all fields specified."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_defaults",
    input=CaseInput(json_body=_MINIMAL_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def update_work_item_ok_defaults():
    """Happy path: minimal body, operator uses default value."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_dima_id",
    input=CaseInput(json_body={"url": "https://example.com"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_missing_dima_id():
    """Error path: dimaId is required but missing → 422."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_url",
    input=CaseInput(json_body={"dimaId": "2026052400116279290"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_missing_url():
    """Error path: url is required but missing → 422."""


def _seed_error(world):
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _FailingDimaService:
        def create_work_item(self, _staff_id, _request_body):
            raise Exception("DIMA API error [500]: upstream timeout")

        def create_work_item_relation(self, _operator, _request_body):
            raise Exception("DIMA API error [500]: upstream timeout")

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_FailingDimaService,
        scope=None,
    )


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="service_error",
    input=CaseInput(json_body=_FULL_BODY),
    seed=_seed_error,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "code": "500"},
    ),
)
def update_work_item_service_error():
    """Error path: upstream DIMA call fails, router returns error envelope."""


_HTTP_BODY = {
    "dimaId": "2026052400116279290",
    "url": "http://example.com/path",
}


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_http_scheme",
    input=CaseInput(json_body=_HTTP_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def update_work_item_ok_http_scheme():
    """HTTP URL remains supported for DIMA URL relation passthrough."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="invalid_url_file_scheme",
    input=CaseInput(json_body={
        "dimaId": "2026052400116279290",
        "url": "file:///etc/passwd",
    }),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_invalid_url_file_scheme():
    """Reject file:// URL."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="invalid_url_gopher_scheme",
    input=CaseInput(json_body={
        "dimaId": "2026052400116279290",
        "url": "gopher://127.0.0.1:6379/_xxx",
    }),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_invalid_url_gopher_scheme():
    """Reject gopher:// URL."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="invalid_url_ftp_scheme",
    input=CaseInput(json_body={
        "dimaId": "2026052400116279290",
        "url": "ftp://example.com/a.zip",
    }),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_invalid_url_ftp_scheme():
    """Reject ftp:// URL."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="invalid_url_javascript_scheme",
    input=CaseInput(json_body={
        "dimaId": "2026052400116279290",
        "url": "javascript:alert(1)",
    }),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_invalid_url_javascript_scheme():
    """Reject javascript: URL."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="invalid_url_data_scheme",
    input=CaseInput(json_body={
        "dimaId": "2026052400116279290",
        "url": "data:text/plain,hello",
    }),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_invalid_url_data_scheme():
    """Reject data: URL."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="invalid_url_blank",
    input=CaseInput(json_body={
        "dimaId": "2026052400116279290",
        "url": "   ",
    }),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_invalid_url_blank():
    """Reject blank URL after trimming."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="invalid_url_too_long",
    input=CaseInput(json_body={
        "dimaId": "2026052400116279290",
        "url": "https://example.com/" + "a" * 4096,
    }),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_invalid_url_too_long():
    """Reject overlong URL."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="invalid_url_missing_hostname",
    input=CaseInput(json_body={
        "dimaId": "2026052400116279290",
        "url": "https:///path-without-host",
    }),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_invalid_url_missing_hostname():
    """Reject HTTP(S) URL without hostname."""
