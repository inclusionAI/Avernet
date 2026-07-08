"""Endpoint tests for POST /api/public/dima/arkgw/file/upload.

Tests use the DI test framework with stub WorkspaceHostingClient bindings.

- **ok_file**: upload a file via stub, returns success.
- **ok_url**: url转存 via stub, returns success.
- **ok_defaults**: no staffId/sourceId provided, defaults apply.
- **no_file_no_url**: neither file nor url → 400.
- **file_too_large**: file exceeds 10MB → 400.
- **service_error**: stub raises; router returns ``{"success": False, "code": "500", ...}``.
"""
from __future__ import annotations

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/public/dima/arkgw/file/upload"


def _seed_ok(world):
    """Override the binding with a stub that returns a canned success."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _StubDimaService:
        def create_work_item(self, staff_id, request_body):
            return {"success": True, "code": "200", "message": "OK", "data": None}

        def create_work_item_relation(self, operator, request_body):
            return {"success": True, "code": "200", "message": "OK", "data": None}

        def upload_file_to_arkgw(self, **kwargs):
            return {
                "success": True,
                "code": "ARK_RS_100000200",
                "message": "",
                "data": {
                    "fileId": "202407080010000519401",
                    "fileName": kwargs.get("file_name") or "image.png",
                    "fileSize": "2.38KB",
                    "url": "https://oss.example.com/file.pdf",
                },
            }

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_StubDimaService,
        scope=None,
    )


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_file",
    input=CaseInput(
        form_data={"staffId": "100000", "sourceId": "agentCoding"},
        files=[("file", ("test.pdf", b"%PDF-1.4 fake content"))],
    ),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "ARK_RS_100000200"},
    ),
)
def arkgw_upload_ok_file():
    """Happy path: file uploaded via stub WorkspaceHostingClient."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_url",
    input=CaseInput(
        form_data={
            "staffId": "100000",
            "sourceId": "agentCoding",
            "url": "https://example.com/image.png",
        },
    ),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "ARK_RS_100000200"},
    ),
)
def arkgw_upload_ok_url():
    """Happy path: url转存 via stub WorkspaceHostingClient."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_defaults",
    input=CaseInput(
        files=[("file", ("test.pdf", b"%PDF-1.4 fake content"))],
    ),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "ARK_RS_100000200"},
    ),
)
def arkgw_upload_ok_defaults():
    """Happy path: staffId/sourceId use defaults when not provided."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="no_file_no_url",
    input=CaseInput(
        form_data={"staffId": "100000", "sourceId": "agentCoding"},
    ),
    seed=_seed_ok,
    expect=ExpectError(
        status=400,
        json_contains={"detail": "file 和 url 不能同时为空"},
    ),
)
def arkgw_upload_no_file_no_url():
    """Validation: neither file nor url → 400."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="file_too_large",
    input=CaseInput(
        form_data={"staffId": "100000", "sourceId": "agentCoding"},
        files=[("file", ("big.bin", b"x" * (10 * 1024 * 1024 + 1)))],
    ),
    seed=_seed_ok,
    expect=ExpectError(
        status=400,
        json_contains={"detail": "文件大小不能超过 10MB"},
    ),
)
def arkgw_upload_file_too_large():
    """Validation: file exceeds 10MB → 400."""


def _seed_error(world):
    """Override the binding with a stub that always raises."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _FailingDimaService:
        def create_work_item(self, _staff_id, _request_body):
            raise Exception("DIMA API error [500]: upstream timeout")

        def create_work_item_relation(self, _operator, _request_body):
            raise Exception("DIMA API error [500]: upstream timeout")

        def upload_file_to_arkgw(self, **kwargs):
            raise Exception("Arkgw upload request failed: Connection timeout")

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_FailingDimaService,
        scope=None,
    )


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="service_error",
    input=CaseInput(
        form_data={"staffId": "100000", "sourceId": "agentCoding"},
        files=[("file", ("test.pdf", b"%PDF-1.4 fake content"))],
    ),
    seed=_seed_error,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "code": "500"},
    ),
)
def arkgw_upload_service_error():
    """Error path: WorkspaceHostingClient raises, router returns error envelope."""
