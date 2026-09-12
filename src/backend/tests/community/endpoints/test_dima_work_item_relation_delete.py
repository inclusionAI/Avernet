"""Endpoint tests for POST /api/public/dima/work-items/relation/delete.

Tests use the DI test framework with stub service bindings.

- **ok**: stub returns success; router wraps as ``{"success": True, ...}``.
- **ok_default_operator**: operator omitted → defaults to "100000".
- **ok_extra_fields**: extra fields in body are passed through (model_config extra=allow).
- **service_error**: stub raises; router returns ``{"success": False, "code": "500", ...}``.
- **missing_relation_identifier**: missing relationIdentifier → 422.
- **missing_relation_record_id**: missing relationRecordId → 422.
"""
from __future__ import annotations

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/public/dima/work-items/relation/delete"

_FULL_BODY = {
    "operator": "100000",
    "relationIdentifier": "URL",
    "relationRecordId": "2024041800100538244",
}

_NO_OPERATOR_BODY = {
    "relationIdentifier": "COMMON",
    "relationRecordId": "2024041700100534432",
}

_EXTRA_BODY = {
    "operator": "382716",
    "relationIdentifier": "URL",
    "relationRecordId": "2026091100110913874",
    "memo": "some extra field",
}


def _seed_ok(world):
    """Override the binding with a stub that returns a canned success."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _StubDimaService:
        def delete_work_item_relation(self, operator, request_body):
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
        json_contains={"success": True, "code": "200"},
    ),
)
def delete_relation_ok():
    """Happy path: full body with all fields specified."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_default_operator",
    input=CaseInput(json_body=_NO_OPERATOR_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def delete_relation_ok_default_operator():
    """Happy path: operator omitted, defaults to '100000'."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_extra_fields",
    input=CaseInput(json_body=_EXTRA_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def delete_relation_ok_extra_fields():
    """Happy path: extra fields (memo) pass through via model_config extra=allow."""


def _seed_error(world):
    """Override the binding with a service that always raises."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _FailingDimaService:
        def delete_work_item_relation(self, _operator, _request_body):
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
def delete_relation_service_error():
    """Error path: upstream DIMA call fails, router returns error envelope."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_relation_identifier",
    input=CaseInput(json_body={"relationRecordId": "2024041800100538244"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def delete_relation_missing_relation_identifier():
    """Validation: missing relationIdentifier returns 422."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_relation_record_id",
    input=CaseInput(json_body={"relationIdentifier": "URL"}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def delete_relation_missing_relation_record_id():
    """Validation: missing relationRecordId returns 422."""
