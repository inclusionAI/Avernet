"""Endpoint tests for GET /api/public/dima/work-items/relation/list.

Tests use the DI test framework with stub service bindings.

- **ok**: stub returns grouped relation data; router wraps as ``{"success": True, ...}``.
- **ok_default_operator**: operator query param omitted → defaults to "100000".
- **ok_empty**: stub returns no relations → data is empty dict.
- **service_error**: stub raises; router returns ``{"success": False, "code": "500", ...}``.
- **missing_work_item_id**: missing workItemId query param → 422.
"""
from __future__ import annotations

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/public/dima/work-items/relation/list"

_FULL_QUERY = {"workItemId": "2026091100118991620", "operator": "382716"}

_NO_OPERATOR_QUERY = {"workItemId": "2026091100118991620"}

_CANNED_DATA = {
    "URL": [
        {
            "relationId": "RELATION00100000002",
            "relationRecordId": "2024041800100538244",
            "relationName": "链接",
            "subject": "Dima Project 0418 迭代",
            "url": "https://linke.alipay.com/#/alipay/iteration/detail/EI63434623",
        }
    ],
    "SUB": [],
    "PARENT": [],
    "COMMON": [],
    "ATTACHMENT": [],
}


def _seed_ok(world):
    """Override the binding with a stub that returns canned relation data."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _StubDimaService:
        def list_work_item_relations(self, work_item_id, operator="100000"):
            return {
                "success": True,
                "code": "ARK_RS_100000200",
                "message": "",
                "data": _CANNED_DATA,
            }

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_StubDimaService,
        scope=None,
    )


def _seed_ok_empty(world):
    """Override the binding with a stub that returns empty relation data."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _StubDimaService:
        def list_work_item_relations(self, work_item_id, operator="100000"):
            return {
                "success": True,
                "code": "200",
                "message": "OK",
                "data": {},
            }

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_StubDimaService,
        scope=None,
    )


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="ok",
    input=CaseInput(query_params=_FULL_QUERY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def list_relation_ok():
    """Happy path: full query params, stub returns grouped relation data."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="ok_default_operator",
    input=CaseInput(query_params=_NO_OPERATOR_QUERY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def list_relation_ok_default_operator():
    """Happy path: operator omitted, defaults to '100000'."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="ok_empty",
    input=CaseInput(query_params=_NO_OPERATOR_QUERY),
    seed=_seed_ok_empty,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def list_relation_ok_empty():
    """Happy path: no relations found, data is empty dict."""


def _seed_error(world):
    """Override the binding with a service that always raises."""
    from agentclaw.community.api.workitem_service import WorkItemServiceProtocol

    class _FailingDimaService:
        def list_work_item_relations(self, _work_item_id, _operator="100000"):
            raise Exception("DIMA API error [500]: upstream timeout")

    world.injector.binder.bind(
        WorkItemServiceProtocol,
        to=_FailingDimaService,
        scope=None,
    )


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="service_error",
    input=CaseInput(query_params=_FULL_QUERY),
    seed=_seed_error,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "code": "500"},
    ),
)
def list_relation_service_error():
    """Error path: upstream DIMA call fails, router returns error envelope."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="missing_work_item_id",
    input=CaseInput(query_params={}),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def list_relation_missing_work_item_id():
    """Validation: missing workItemId query param returns 422."""
