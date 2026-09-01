"""Unit tests for opt-in work-order decision callbacks."""

import json
from datetime import datetime
from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.work_orders.callbacks import (
    FriendDecisionCallbackHandler,
    WorkOrderCallbackCredential,
    WorkOrderDecisionCallbackDispatcher,
    friend_request_id,
    validate_friend_approval_event,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderCallbackError,
    WorkOrderInvalidEventError,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderApprovalContext,
    WorkOrderApproverRecord,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderEventType,
    WorkOrderRecord,
    WorkOrderStatus,
)
from agentclaw.community.plugin_api.http_client import HttpClient


NOW = datetime(2026, 8, 27, 10, 0, 0)


def _context(
    *,
    event_type: WorkOrderEventType = WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED,
    biz_type: str = WorkOrderBizType.BOT_FRIEND.value,
    biz_data: str | None = json.dumps({"request_ids": ["request/77"]}),
) -> WorkOrderApprovalContext:
    order = WorkOrderRecord(
        id=11,
        work_order_no="WO-11",
        biz_type=biz_type,
        biz_id="legacy-id",
        biz_data=biz_data,
        applicant_user_id="applicant-1",
        apply_reason=None,
        status=WorkOrderStatus.PENDING,
        reviewer_user_id=None,
        review_remark=None,
        reviewed_at=None,
        env="dev",
        gmt_created=NOW,
        gmt_modified=NOW,
    )
    return WorkOrderApprovalContext(
        work_order=order,
        approver=WorkOrderApproverRecord(
            id=21,
            work_order_id=11,
            approver_user_id="reviewer-1",
            status=WorkOrderApproverStatus.PENDING,
            review_remark=None,
            reviewed_at=None,
            env="dev",
            gmt_created=NOW,
            gmt_modified=NOW,
        ),
        source_event_type=event_type.value,
    )


def _response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", "https://bcn.test/callback"),
    )


def test_friend_handler_accepts_and_forwards_only_supplied_credential() -> None:
    http = MagicMock(spec=HttpClient)
    http.post.return_value = _response({"success": True, "data": {}})
    handler = FriendDecisionCallbackHandler(http, timeout=3.0)
    credential = WorkOrderCallbackCredential(
        headers={"Authorization": "Bearer token", "X-Trace-Id": "trace"}
    )

    handler.handle(
        context=_context(),
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        credential=credential,
    )

    http.post.assert_called_once_with(
        "/collaboration/friend-connections/requests/request%2F77/accept",
        json=None,
        headers={"Authorization": "Bearer token", "X-Trace-Id": "trace"},
        timeout=3.0,
    )


def test_friend_handler_logs_bcn_response_details_without_credentials(caplog) -> None:
    caplog.set_level("INFO", logger="start")
    http = MagicMock(spec=HttpClient)
    http.post.return_value = _response(
        {
            "code": 403201,
            "message": "Forbidden",
            "data": None,
            "request_id": "bcn-request-1",
        },
        status_code=403,
    )
    handler = FriendDecisionCallbackHandler(http)

    with pytest.raises(WorkOrderCallbackError):
        handler.handle(
            context=_context(),
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            credential=WorkOrderCallbackCredential(
                headers={
                    "Authorization": "Bearer secret-auth",
                    "X-Avernet-Principal": "secret-principal",
                }
            ),
        )

    request_log = next(
        record
        for record in caplog.records
        if record.message == "friend work-order BCN callback request"
    )
    response_log = next(
        record
        for record in caplog.records
        if record.message == "friend work-order BCN callback response"
    )
    assert request_log.request_body is None
    assert request_log.has_authorization is True
    assert request_log.has_x_avernet_principal is True
    assert response_log.http_status == 403
    assert response_log.response_code == 403201
    assert response_log.response_message == "Forbidden"
    assert response_log.response_request_id == "bcn-request-1"
    assert response_log.response_body_raw == (
        '{"code":403201,"message":"Forbidden","data":null,'
        '"request_id":"bcn-request-1"}'
    )
    assert "secret-auth" not in caplog.text
    assert "secret-principal" not in caplog.text


def test_friend_handler_logs_non_json_bcn_response(caplog) -> None:
    caplog.set_level("INFO", logger="start")
    http = MagicMock(spec=HttpClient)
    http.post.return_value = httpx.Response(
        502,
        text="Bad Gateway",
        request=httpx.Request("POST", "https://bcn.test/callback"),
    )
    handler = FriendDecisionCallbackHandler(http)

    with pytest.raises(WorkOrderCallbackError):
        handler.handle(
            context=_context(),
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            credential=WorkOrderCallbackCredential(headers={}),
        )

    response_log = next(
        record
        for record in caplog.records
        if record.message == "friend work-order BCN callback response"
    )
    assert response_log.http_status == 502
    assert response_log.response_code is None
    assert response_log.response_body_raw == "Bad Gateway"


def test_friend_handler_rejects_with_review_reason() -> None:
    http = MagicMock(spec=HttpClient)
    http.post.return_value = _response({"success": True})
    handler = FriendDecisionCallbackHandler(http)

    handler.handle(
        context=_context(event_type=WorkOrderEventType.BOT2BOT_FRIEND_APPLIED),
        decision=WorkOrderDecision.REJECTED,
        review_remark="not allowed",
        credential=WorkOrderCallbackCredential(headers={}),
    )

    assert http.post.call_args.kwargs["json"] == {"reason": "not allowed"}
    assert http.post.call_args.args[0].endswith("/reject")


@pytest.mark.parametrize(
    "response",
    [
        _response({"success": False}),
        _response({"data": {}}, status_code=200),
        _response({"success": True}, status_code=500),
    ],
)
def test_friend_handler_turns_every_upstream_failure_into_callback_error(
    response: httpx.Response,
) -> None:
    http = MagicMock(spec=HttpClient)
    http.post.return_value = response
    handler = FriendDecisionCallbackHandler(http)

    with pytest.raises(WorkOrderCallbackError):
        handler.handle(
            context=_context(),
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            credential=WorkOrderCallbackCredential(headers={}),
        )


def test_friend_handler_rejects_wrong_biz_type_before_http() -> None:
    http = MagicMock(spec=HttpClient)
    handler = FriendDecisionCallbackHandler(http)

    with pytest.raises(WorkOrderInvalidEventError, match="BOT_FRIEND"):
        handler.handle(
            context=_context(biz_type="OTHER"),
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            credential=WorkOrderCallbackCredential(headers={}),
        )

    http.post.assert_not_called()


@pytest.mark.parametrize("biz_data", [None, "not-json", "[]"])
def test_friend_handler_rejects_invalid_persisted_json(biz_data: str | None) -> None:
    http = MagicMock(spec=HttpClient)
    handler = FriendDecisionCallbackHandler(http)

    with pytest.raises(WorkOrderInvalidEventError):
        handler.handle(
            context=_context(biz_data=biz_data),
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            credential=WorkOrderCallbackCredential(headers={}),
        )

    http.post.assert_not_called()


def test_dispatcher_is_noop_for_unregistered_event() -> None:
    http = MagicMock(spec=HttpClient)
    dispatcher = WorkOrderDecisionCallbackDispatcher(http)
    context = _context().model_copy(update={"source_event_type": "OTHER_APPLIED"})

    assert dispatcher.requires_callback(context.source_event_type) is False
    dispatcher.dispatch(
        context=context,
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        credential=WorkOrderCallbackCredential(headers={}),
    )

    http.post.assert_not_called()


def test_dispatcher_registers_both_friend_events() -> None:
    dispatcher = WorkOrderDecisionCallbackDispatcher(MagicMock(spec=HttpClient))

    assert dispatcher.requires_callback(
        WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value
    )
    assert dispatcher.requires_callback(WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value)


def test_dispatcher_invokes_registered_friend_handler() -> None:
    http = MagicMock(spec=HttpClient)
    http.post.return_value = _response({"success": True})
    dispatcher = WorkOrderDecisionCallbackDispatcher(http)

    dispatcher.dispatch(
        context=_context(),
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        credential=WorkOrderCallbackCredential(headers={}),
    )

    http.post.assert_called_once()


def test_creation_validation_ignores_non_friend_events() -> None:
    validate_friend_approval_event(
        biz_type="SKILL_COLLABORATOR",
        event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        biz_data=None,
    )
    validate_friend_approval_event(
        biz_type="ANY",
        event_type="UNKNOWN",
        biz_data=None,
    )


def test_creation_validation_rejects_friend_event_with_wrong_biz_type() -> None:
    with pytest.raises(WorkOrderInvalidEventError, match="BOT_FRIEND"):
        validate_friend_approval_event(
            biz_type="OTHER",
            event_type=WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
            biz_data={"request_ids": ["request-1"]},
        )


def test_creation_validation_accepts_friend_contract_and_trims_id() -> None:
    biz_data = {"request_ids": [" request-1 "]}

    validate_friend_approval_event(
        biz_type=WorkOrderBizType.BOT_FRIEND.value,
        event_type=WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
        biz_data=biz_data,
    )

    assert friend_request_id(biz_data) == "request-1"
