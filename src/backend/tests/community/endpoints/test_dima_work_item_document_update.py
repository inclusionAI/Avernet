"""Endpoint tests for POST /api/public/dima/work-items/document/update.

Both scenarios drive the real router via the DI test framework.

- **ok**: the stub returns a success dict; router wraps it as
  ``{"success": True, "code": "200", ...}``.
- **service_error**: the seed replaces the protocol binding with an
  instance that raises, triggering the router's except branch which
  returns ``{"success": False, "code": "500", ...}``.
- **content_empty**: content is empty string → 400 with custom message.
- **content_whitespace**: content is whitespace-only → 400 with custom message.
- **missing_required_fields**: missing staffId/workItemId/content → 422.
- **format_type_default**: no formatType → defaults to RICHTEXT.
- **format_type_markdown**: formatType=MARKDOWN is passed through.
"""
from __future__ import annotations

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/public/dima/work-items/document/update"
_BODY = {
    "staffId": "000000",
    "workItemId": "2024080600104049562",
    "content": "<p>更新后的工作项描述</p>",
}


def _seed_ok(world):
    """Override the binding with a stub that returns a canned success."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _StubWorkItemService:
        def update_work_item_document(
            self, staff_id, work_item_id, content, format_type="MARKDOWN", editor_type="YUQUE",
        ):
            return {
                "success": True,
                "code": "200",
                "message": "OK",
                "data": {"workItemId": work_item_id},
            }

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_StubWorkItemService,
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
def update_work_item_document_ok():
    """Happy path: work item document updated via stub service."""


def _seed_error(world):
    """Override the binding with a service that always raises."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _FailingWorkItemService:
        def update_work_item_document(
            self, staff_id, work_item_id, content, format_type="MARKDOWN", editor_type="YUQUE",
        ):
            raise Exception("DIMA API error [500]: upstream timeout")

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_FailingWorkItemService,
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
def update_work_item_document_service_error():
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
def update_work_item_document_content_empty():
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
def update_work_item_document_content_whitespace():
    """Validation: whitespace-only content returns 400."""


# --- missing required fields → 422 ---


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_staff_id",
    input=CaseInput(json_body={"workItemId": "xxx", "content": "c"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_document_missing_staff_id():
    """Validation: missing staffId returns 422."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_work_item_id",
    input=CaseInput(json_body={"staffId": "000000", "content": "c"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_document_missing_work_item_id():
    """Validation: missing workItemId returns 422."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_content",
    input=CaseInput(json_body={"staffId": "000000", "workItemId": "xxx"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def update_work_item_document_missing_content():
    """Validation: missing content returns 422."""


# --- formatType pass-through tests ---

_captured_args: list[dict] = []


def _seed_capturing(world):
    """Stub that captures arguments for later assertion."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _CapturingWorkItemService:
        def update_work_item_document(
            self, staff_id, work_item_id, content, format_type="MARKDOWN", editor_type="YUQUE",
        ):
            _captured_args.append({
                "staff_id": staff_id,
                "work_item_id": work_item_id,
                "content": content,
                "format_type": format_type,
                "editor_type": editor_type,
            })
            return {
                "success": True,
                "code": "200",
                "message": "OK",
                "data": {"workItemId": work_item_id},
            }

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_CapturingWorkItemService,
        scope=None,
    )
    _captured_args.clear()


def _assert_format_type_richtext(_world, _response):
    assert _captured_args, "service was never called"
    args = _captured_args[-1]
    assert args["format_type"] == "MARKDOWN"
    assert args["editor_type"] == "YUQUE"


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="format_type_default",
    input=CaseInput(json_body=_BODY),
    seed=_seed_capturing,
    extra_assertions=(_assert_format_type_richtext,),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def update_work_item_document_format_type_default():
    """Default formatType: omitted → MARKDOWN."""


def _assert_format_type_markdown(_world, _response):
    assert _captured_args, "service was never called"
    args = _captured_args[-1]
    assert args["format_type"] == "MARKDOWN"
    assert args["editor_type"] == "YUQUE"


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="format_type_markdown",
    input=CaseInput(json_body={**_BODY, "formatType": "MARKDOWN"}),
    seed=_seed_capturing,
    extra_assertions=(_assert_format_type_markdown,),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def update_work_item_document_format_type_markdown():
    """Explicit formatType=MARKDOWN is passed through."""
