"""Endpoint tests for POST /api/public/dima/work-items/relation/create.

Tests use the DI test framework with stub service bindings.

- **ok**: stub returns success; router wraps as ``{"success": True, ...}``.
- **ok_defaults**: minimal body (only sourceIdentifier + toValue) uses defaults (type=common).
- **ok_custom_overrides**: user-provided values override defaults.
- **ok_type_url**: type=url sets URL-specific defaults.
- **ok_type_url_with_overrides**: type=url but user overrides some fields.
- **ok_type_common_explicit**: type=common explicitly, same as default.
- **service_error**: stub raises; router returns ``{"success": False, "code": "500", ...}``.
"""
from __future__ import annotations

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/public/dima/work-items/relation/create"

_FULL_BODY = {
    "operator": "100000",
    "relationIdentifier": "COMMON",
    "sourceIdentifier": "2026052400116279290",
    "relationId": "20001",
    "toValue": "2026052300116278019",
    "toCategory": "Req",
}

_MINIMAL_BODY = {
    "sourceIdentifier": "2026052400116279290",
    "toValue": "2026052300116278019",
}

_CUSTOM_BODY = {
    "sourceIdentifier": "2026052400116279290",
    "toValue": "2026052300116278019",
    "relationIdentifier": "CUSTOM",
    "relationId": "99999",
    "toCategory": "Custom",
}

_URL_TYPE_BODY = {
    "type": "url",
    "sourceIdentifier": "2026052400116279290",
    "toValue": "https://example.com",
}

_URL_TYPE_OVERRIDE_BODY = {
    "type": "url",
    "sourceIdentifier": "2026052400116279290",
    "toValue": "https://example.com",
    "relationIdentifier": "CUSTOM_REL",
    "toCategory": "CustomCat",
}

_COMMON_TYPE_EXPLICIT_BODY = {
    "type": "common",
    "sourceIdentifier": "2026052400116279290",
    "toValue": "2026052300116278019",
}


def _seed_ok(world):
    """Override the binding with a stub that returns a canned success."""
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
        json_contains={"success": True, "code": "200"},
    ),
)
def create_relation_ok():
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
def create_relation_ok_defaults():
    """Happy path: minimal body, defaults for operator/relationIdentifier/relationId/toCategory."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_custom_overrides",
    input=CaseInput(json_body=_CUSTOM_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def create_relation_ok_custom_overrides():
    """Happy path: user overrides default values."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_type_url",
    input=CaseInput(json_body=_URL_TYPE_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def create_relation_ok_type_url():
    """Happy path: type=url sets URL-specific defaults (relationIdentifier=URL, relationId=RELATION00100000002, toCategory=Url)."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_type_url_with_overrides",
    input=CaseInput(json_body=_URL_TYPE_OVERRIDE_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def create_relation_ok_type_url_with_overrides():
    """Happy path: type=url but user overrides relationIdentifier and toCategory."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_type_common_explicit",
    input=CaseInput(json_body=_COMMON_TYPE_EXPLICIT_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "code": "200"},
    ),
)
def create_relation_ok_type_common_explicit():
    """Happy path: type=common explicitly, same behavior as omitting type."""


def _seed_error(world):
    """Override the binding with a service that always raises."""
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
def create_relation_service_error():
    """Error path: upstream DIMA call fails, router returns error envelope."""
