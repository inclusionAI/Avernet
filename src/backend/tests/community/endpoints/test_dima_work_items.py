"""Endpoint tests for POST /api/public/dima/work-items/create.

Both scenarios drive the real router via the DI test framework.
DIMA calls are NOT overridden in local mode — they hit the real DIMA
OpenAPI.  Tests seed a stub binding so no outbound call leaves the
test process.

- **ok**: the stub returns a success dict; router wraps it as
  ``{"success": True, "code": "200", ...}``.
- **service_error**: the seed replaces the protocol binding with an
  instance that raises, triggering the router's except branch which
  returns ``{"success": False, "code": "500", ...}``.
- **content_empty**: content is empty string → 400 with custom message.
- **content_whitespace**: content is whitespace-only → 400 with custom message.
- **missing_required_fields**: missing staffId/workspaceId/subject/content → 422.
- **format_type_default**: no formatType → workItemDocument.formatType == MARKDOWN.
- **format_type_richtext**: formatType=RICHTEXT → workItemDocument.formatType == RICHTEXT.
"""
from __future__ import annotations

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/public/dima/work-items/create"
_BODY = {
    "staffId": "000000",
    "workspaceId": "WS001",
    "subject": "test work item",
    "content": "<p>详情描述</p>",
}


def _seed_ok(world):
    """Override the binding with a stub that returns a canned success."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _StubDimaService:
        def create_work_item(self, staff_id, request_body):
            return {
                "success": True,
                "code": "200",
                "message": "OK",
                "data": {"identifier": "20240806001"},
            }

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_StubDimaService,
        scope=None,
    )


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok",
    input=CaseInput(json_body=_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def create_work_item_ok():
    """Happy path: DIMA work item created via stub service."""


def _seed_error(world):
    """Override the binding with a service that always raises."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _FailingDimaService:
        def create_work_item(self, _staff_id, _request_body):
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
    input=CaseInput(json_body=_BODY),
    seed=_seed_error,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "code": "500"},
    ),
)
def create_work_item_service_error():
    """Error path: upstream DIMA call fails, router returns error envelope."""


# --- content validation tests ---


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="content_empty",
    input=CaseInput(json_body={**_BODY, "content": ""}),
    seed=_seed_ok,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "code": "400"},
    ),
)
def create_work_item_content_empty():
    """Validation: empty content string returns 400."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="content_whitespace",
    input=CaseInput(json_body={**_BODY, "content": "   "}),
    seed=_seed_ok,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "code": "400"},
    ),
)
def create_work_item_content_whitespace():
    """Validation: whitespace-only content returns 400."""


# --- missing required fields → 422 ---


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_staff_id",
    input=CaseInput(json_body={"workspaceId": "WS001", "subject": "t", "content": "c"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def create_work_item_missing_staff_id():
    """Validation: missing staffId returns 422."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_workspace_id",
    input=CaseInput(json_body={"staffId": "000000", "subject": "t", "content": "c"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def create_work_item_missing_workspace_id():
    """Validation: missing workspaceId returns 422."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_subject",
    input=CaseInput(json_body={"staffId": "000000", "workspaceId": "WS001", "content": "c"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def create_work_item_missing_subject():
    """Validation: missing subject returns 422."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_content",
    input=CaseInput(json_body={"staffId": "000000", "workspaceId": "WS001", "subject": "t"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def create_work_item_missing_content():
    """Validation: missing content returns 422."""


# --- formatType tests ---

_captured_bodies: list[dict] = []


def _seed_capturing(world):
    """Stub that captures request_body for later assertion."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _CapturingDimaService:
        def create_work_item(self, staff_id, request_body):
            _captured_bodies.append(request_body)
            return {
                "success": True,
                "code": "200",
                "message": "OK",
                "data": {"identifier": "20240806002"},
            }

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_CapturingDimaService,
        scope=None,
    )
    _captured_bodies.clear()


def _assert_format_type_markdown(_world, _response):
    assert _captured_bodies, "service was never called"
    doc = _captured_bodies[-1]["workItemDocument"]
    assert doc["formatType"] == "MARKDOWN"
    assert doc["editorType"] == "YUQUE"


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="format_type_default",
    input=CaseInput(json_body=_BODY),
    seed=_seed_capturing,
    extra_assertions=(_assert_format_type_markdown,),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def create_work_item_format_type_default():
    """Default formatType: omitted → MARKDOWN."""


def _assert_format_type_richtext(_world, _response):
    assert _captured_bodies, "service was never called"
    doc = _captured_bodies[-1]["workItemDocument"]
    assert doc["formatType"] == "RICHTEXT"
    assert doc["editorType"] == "YUQUE"


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="format_type_richtext",
    input=CaseInput(json_body={**_BODY, "formatType": "RICHTEXT"}),
    seed=_seed_capturing,
    extra_assertions=(_assert_format_type_richtext,),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def create_work_item_format_type_richtext():
    """Explicit formatType=RICHTEXT is passed through."""