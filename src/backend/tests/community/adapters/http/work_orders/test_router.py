"""Contract tests for the authenticated HTTP work-order event mirror."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.work_orders.router import router
from agentclaw.community.api.work_order_service import WorkOrderServiceProtocol
from agentclaw.community.core.errors import (
    DomainError,
    LoginRedirectRequired,
    Unauthorized,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderInvalidEventError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
)

_PATH = "/api/v1/work-orders/events"
_USER = AuthenticatedUser(
    id="identity-1",
    staffId="staff-1",
    operatorName="operator-1",
)


def _result(
    category: NotificationCategory = NotificationCategory.APPROVAL,
) -> WorkOrderEventCreatedResult:
    return WorkOrderEventCreatedResult(
        event_category=category,
        work_order_id=11 if category == NotificationCategory.APPROVAL else None,
        work_order_no="WO-11" if category == NotificationCategory.APPROVAL else None,
        notification_ids=[21],
        status=(
            WorkOrderEventStatus.PENDING
            if category == NotificationCategory.APPROVAL
            else WorkOrderEventStatus.CREATED
        ),
    )


def _approval_payload() -> dict[str, object]:
    return {
        "event_category": "APPROVAL",
        "biz_type": "SPACE_JOIN",
        "biz_id": "7",
        "event_type": "SPACE_JOIN_APPLIED",
        "applicant_user_id": "staff-1",
        "approver_user_ids": ["approver-1"],
        "recipient_user_ids": [],
        "title": "Join request",
        "content": {"message": "apply", "items": [1, True]},
        "apply_reason": "please approve",
        "biz_data": {"space_id": 7, "meta": {"source": "web"}},
    }


@pytest.fixture
def service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def app(service: MagicMock) -> FastAPI:
    class _Bindings(Module):
        def configure(self, binder) -> None:
            binder.bind(WorkOrderServiceProtocol, to=service)

    test_app = FastAPI()
    test_app.include_router(router)

    async def _authenticated(request: Request) -> AuthenticatedUser:
        request.state.trace_id = "trace-work-order"
        return _USER

    test_app.dependency_overrides[get_current_user] = _authenticated
    attach_injector(test_app, Injector([_Bindings()]))

    # Use the assembled application's handlers instead of duplicating their
    # behavior. Importing here avoids building the full application at module
    # collection time.
    from agentclaw.community.adapters.http.app import (
        _domain_error_handler,
        _http_exception_handler,
        _unhandled_exception_handler,
        _validation_error_handler,
    )

    test_app.add_exception_handler(DomainError, _domain_error_handler)
    test_app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    test_app.add_exception_handler(RequestValidationError, _validation_error_handler)
    test_app.add_exception_handler(Exception, _unhandled_exception_handler)
    return test_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_create_approval_event_uses_authenticated_actor_and_shared_contract(
    client: TestClient,
    service: MagicMock,
) -> None:
    service.create_work_order_event.return_value = _result()

    response = client.post(_PATH, json=_approval_payload())

    assert response.status_code == 201
    assert response.json() == {
        "code": 201000,
        "message": "Created",
        "data": {
            "event_category": "APPROVAL",
            "work_order_id": 11,
            "work_order_no": "WO-11",
            "notification_ids": [21],
            "status": "PENDING",
        },
        "request_id": "trace-work-order",
    }
    service.create_work_order_event.assert_called_once_with(
        event_category=NotificationCategory.APPROVAL,
        biz_type="SPACE_JOIN",
        biz_id="7",
        event_type="SPACE_JOIN_APPLIED",
        applicant_user_id="staff-1",
        approver_user_ids=["approver-1"],
        recipient_user_ids=[],
        title="Join request",
        content={"message": "apply", "items": [1, True]},
        apply_reason="please approve",
        biz_data={"space_id": 7, "meta": {"source": "web"}},
        actor_id="staff-1",
    )


def test_create_notice_event_returns_created_status(
    client: TestClient,
    service: MagicMock,
) -> None:
    service.create_work_order_event.return_value = _result(NotificationCategory.NOTICE)
    payload = _approval_payload() | {
        "event_category": "NOTICE",
        "applicant_user_id": None,
        "approver_user_ids": [],
        "recipient_user_ids": ["recipient-1"],
    }

    response = client.post(_PATH, json=payload)

    assert response.status_code == 201
    assert response.json()["data"] == {
        "event_category": "NOTICE",
        "work_order_id": None,
        "work_order_no": None,
        "notification_ids": [21],
        "status": "CREATED",
    }


@pytest.mark.parametrize(
    ("exc", "status", "code", "message"),
    [
        (
            WorkOrderInvalidEventError("internal validation detail"),
            400,
            400201,
            "Invalid work-order event",
        ),
        (
            WorkOrderAccessDeniedError("internal actor detail"),
            403,
            403201,
            "Forbidden",
        ),
        (
            WorkOrderAlreadyPendingError("internal pending detail"),
            409,
            409201,
            "A pending application already exists",
        ),
    ],
)
def test_mapped_business_errors_use_existing_envelope_codes(
    client: TestClient,
    service: MagicMock,
    exc: Exception,
    status: int,
    code: int,
    message: str,
) -> None:
    service.create_work_order_event.side_effect = exc

    response = client.post(_PATH, json=_approval_payload())

    assert response.status_code == status
    assert response.json() == {
        "code": code,
        "message": message,
        "data": None,
        "request_id": "trace-work-order",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        _approval_payload() | {"content": [1, 2]},
        _approval_payload() | {"biz_data": "not-an-object"},
    ],
)
def test_validation_errors_are_enveloped(
    client: TestClient,
    service: MagicMock,
    payload: dict[str, object],
) -> None:
    response = client.post(_PATH, json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422000
    assert body["message"] == "Invalid request"
    assert body["data"] is None
    assert "detail" not in body
    service.create_work_order_event.assert_not_called()


def test_unauthenticated_request_is_enveloped(
    app: FastAPI,
    service: MagicMock,
) -> None:
    async def _unauthenticated() -> AuthenticatedUser:
        raise Unauthorized("missing identity")

    app.dependency_overrides[get_current_user] = _unauthenticated
    response = TestClient(app, raise_server_exceptions=False).post(
        _PATH, json=_approval_payload()
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": 401000,
        "message": "Unauthorized",
        "data": None,
        "request_id": "",
    }
    service.create_work_order_event.assert_not_called()


def test_login_redirect_semantics_are_preserved(
    app: FastAPI,
    service: MagicMock,
) -> None:
    async def _login_redirect() -> AuthenticatedUser:
        raise LoginRedirectRequired("restart login")

    app.dependency_overrides[get_current_user] = _login_redirect
    response = TestClient(app, raise_server_exceptions=False).post(
        _PATH, json=_approval_payload(), follow_redirects=False
    )

    assert response.status_code == 302
    assert response.json() == {
        "code": 302000,
        "message": "Found",
        "data": None,
        "request_id": "",
    }
    service.create_work_order_event.assert_not_called()


def test_unexpected_error_is_enveloped_without_leaking_details(
    client: TestClient,
    service: MagicMock,
) -> None:
    service.create_work_order_event.side_effect = RuntimeError("secret database URL")

    response = client.post(_PATH, json=_approval_payload())

    assert response.status_code == 500
    assert response.json() == {
        "code": 500000,
        "message": "Internal Server Error",
        "data": None,
        "request_id": "trace-work-order",
    }
    assert "secret database URL" not in response.text


def test_wrong_method_uses_envelope_and_preserves_allow_header(
    client: TestClient,
) -> None:
    response = client.get(_PATH)

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
    assert response.json()["code"] == 405000
    assert response.json()["message"] == "Method Not Allowed"
    assert response.json()["data"] is None
